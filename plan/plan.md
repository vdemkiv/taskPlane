# R-0001 Implementation Plan — Approved T17 withdrawal and Build continuation

## Approved bounded T18 production-wiring scope correction

The standing night-mode delegation authorizes the exact packet `.taskplane/human-gates/T18-production-wiring-scope.md`, SHA-256 `bc965c09c4c85f807170a8ff398cc03561d98654f855d446ebe03094cf03e733`, recorded in its adjacent JSON. This amendment appends only `taskplane/loop.py`, `agents/spec-phase-definitions.json`, `taskplane/tests/test_r0001_phase_agents_spec.py`, and `taskplane/tests/test_r0001_phase_cutover.py` to T18's four existing scope paths. It permits the approved J7 producer outputs to cross the actual Plan/Build boundaries; the two added test paths cover only directly affected fixtures/assertions. J7 must consume actual source coverage, decomposition, Plan, seam manifest, Build, and conformance outputs, then repeat the same public entrypoint with one named seam severed.

Every other T18 field and every other existing JSON value stays unchanged, including its two selectors, 600-second timeout, `T08/T15C/T16` dependencies, all 23 active task identities, all 18 accepted declarations, approved Design, `withdrawal_amendment`, J1–J7, and W01–W34. Prior Plan fingerprint is `d8c17f3d551693af6369948ab9a1eeb68124233111616d06eff6a34280084703`; its raw SHA-256 is `0b6626e19f0a6b7b4da24ba9333c30d0c4dfbef0fe7d086d94b37f11c9eed36c`. The first T18 result `569c4c3051c96394c31c8d147c035fe0c18e6517b61b88eaac981dc7a91d4dec` remains FAIL with no code changes: the absent J7 test file caused exit 4 and zero tests ran. T17/J0 stays withdrawn and unproved, without a replacement fresh-install, account-login, or browser test. Review remains deferred/non-judged; historical reviews supply no fresh PASS. Root owns continuity and gates, retaining the original full-wave EM baseline `a45aae112bd5b6113292771204ca9ff867bf7ff8`, authenticated root, counters, usage, allowance, and start.

One installed Taskplane 2.19.0 impact call with `TASKPLANE_TASK=task_ff822831` covered exactly T18's eight amended paths. It touched `agents`, `taskplane`, and `taskplane/tests`, reported 31 impacted modules and no unknown modules, and retained local depth 3, contract-only boundaries, contract depth 1, and requirement depth 1. Graph fingerprint `53a653dca5579186a128d4e6a2ad6db3d8be7eaad73dc335a281b8db46fff1b7` records the current candidate revision `c63aabc3bb303d7c68e4873ea4368d20a06baca3`; `truncated=true`, `depth_truncated=false`, with requirement-depth blocks for R-0001/R-0003. This bounded result cannot establish complete coverage or conformance. No scan, repair, repeat, new lens, agent, test, or Design restart was performed. The emitted Python Packaging and DevOps and Security sections were read and their file hash verified as `cb9e7ebda7fd7ac1ed00ab45d9121957aae55c328b3ee72b74a8aded6aa216b7`.

## Current approved T17 withdrawal

The user's explicit “do not create this test at all, it does not make sense!!!” followed by “resume build” withdraws T17 and its J0 fresh-clone/empty-Codex-home/browser-login test. The exact approved packet is `.taskplane/human-gates/T17-withdrawal-resume.md`, SHA-256 `746e6773f4fd31e91c6ba6b25e4e07ddb1be1c6512c74c7b38a7b0495e94e68f`, with attributable approval in `.taskplane/human-gates/T17-withdrawal-resume.json`. The introduced test was removed and committed at `c63aabc3bb303d7c68e4873ea4368d20a06baca3`. Do not recreate it, substitute an interactive-login version, require account setup, or promise it as future work. J0 is withdrawn and unproved; its prior failures remain adverse history, never PASS.

The active Plan has 23 tasks. T17 is removed; T18 replaces its sole T17 dependency with T17's original prerequisites in order, `T08, T15C, T16`; T20 removes only T17 and retains `T18, T19, T16B`. All other fields of all 23 surviving tasks are unchanged, including the complete declarations supporting all 18 accepted Build contributions. WAVE-13 is removed, WAVE-14 consumes the three retained prerequisites, and WAVE-16 removes T17. Wave identities are not renumbered. Active journeys are J1–J7; the exact original T17 task, J0 selectors and WAVE-13 declaration remain in `plan/tasks.json#withdrawal_amendment.withdrawn_history`, explicitly outside active scheduling or proof.

FP-AC06 remains an unchanged requirement owned by unchanged T02. This withdrawal does not waive it or create a new pass. J1–J7, all W01–W34 producer/severance selectors and obligations, other negative inventories, fixture-bypass rules, telemetry, rollback, and EM/finalization requirements remain intact.

Approved Design `3b2302b57427c7544cf7909ecf48d1e01f62d1b8fad39f07eb59b8e535aaafe4` and its bytes are unchanged. The canonical action's approved contract equals `design/contract.json`, raw SHA-256 `dc6df4ec264707744a2816113177b46a2182a7ca0aeaf21e250b54cf51045efb`. Only its earlier J0 test/proof requirement and dependent J0 receipt references are superseded by this explicit human withdrawal. This is no claim of J0 conformance, renewed whole-Design approval, or whole-Design conformance. Later Engineering judgment must retain the withdrawal and lack of J0 proof. No unrelated waiver or automatic Design restart is authorized; any mechanical admission conflict must be reported exactly.

Earlier amendment records, review results, and their task counts below remain history. Completed HG-F/HG-E declarations and the accepted T10 declaration retain their original bytes, including historical J0 wording; that wording neither schedules withdrawn J0 nor requires fresh J0 proof for future work, and is not evidence that J0 passed. The existing `plan_review`, `sequencing_amendment`, `human_gates`, `wiring_policy`, `wiring_manifest`, and `budget_policy` JSON values are preserved exactly. The earlier T17 native-entry retry section at the end is withdrawn history and confers no current execution or sign-in authority.

The pre-amendment Plan is retained at `.taskplane/continuity-plan-sources/22f40b0ca44d975b7211b9c47ad7a556b889ac6369493eeb4390e9956aa2788a.json`, canonical fingerprint `22f40b0ca44d975b7211b9c47ad7a556b889ac6369493eeb4390e9956aa2788a`, raw SHA-256 `614252e0ef12969b666cd4823978e8e39e7eb8a01630c48d19af3a2c31a366e8`. All 18 accepted Build records, every earlier failure, the same authenticated root, all cumulative usage, the current 32-hour allowance and original start, and full EM baseline `a45aae112bd5b6113292771204ca9ff867bf7ff8` remain preserved. All earlier lens results remain historical; required independent review is deferred/non-judged. This amendment executes zero new lenses, scans, test/review runs, audits, Design restarts, or subagents. It adds no implementation or publication authority. Root owns continuity validation, mechanical gates, and subsequent Build continuation.

Exactly one installed Taskplane 2.19.0 graph-impact call used `TASKPLANE_TASK=task_3cfbddd1` and one comma-separated `--files` value containing the union of T17/T18/T20 scopes: `taskplane/tests/test_r0001_j0_fresh_install.py,taskplane/graph_decomposition.py,taskplane/plan_topology.py,taskplane/wiring_closure.py,taskplane/tests/test_r0001_j7_decomposition_pipeline.py,taskplane/stage_migration.py,docs/**,taskplane/tests/test_r0001_legacy_retirement.py`. It touched `docs`, `taskplane`, and `taskplane/tests`, reported 31 impacted modules, `unknown=[]`, local depth 3, contract-only boundaries, contract depth 1, and requirement depth 1. `truncated=true`, `depth_truncated=false`; requirement-depth policy blocked further traversal to R-0001/R-0003. Graph fingerprint `d5c7b40338e5eb1296895cb7c087dd3f5ba46691ef77815d367f600c2a11c127` was scanned at `83e741610eb2e2edca3b10ccbd895e47571b5e00`, older than the withdrawal candidate. This is stale and truncated planning context, not complete current coverage or conformance proof; no scan repair or second impact ran. The action-required Packaging and DevOps and Security reference sections were read after verifying whole-file SHA-256 `cb9e7ebda7fd7ac1ed00ab45d9121957aae55c328b3ee72b74a8aded6aa216b7`. Their scanners and lenses were not run.

## Approved one-file T15C scope amendment

The human approval in `.taskplane/human-gates/HG-E-and-T15C-scope-1a338f4.json` approves HG-E on candidate `1a338f4bb517ee31d50239b8cec6986499bb5a34` and exactly appending `taskplane/loop.py` to T15C's existing two-file scope so the actual CLI/loop entry can reach the cutover adapters. T15C remains the sole owner of shared routing and cutover. The pre-edit Plan JSON equals immutable Plan `00a95e546dd3e6ed42216e506800643f38f20b401f9f12324e9e7e6486c83f15`; this scope append is the sole JSON change. Every other task and metadata value, all 17 accepted Build records, W01–W34/J0–J7 proof obligations, rollback, telemetry, native-evidence restrictions, and human gates remain intact. Design `3b2302b57427c7544cf7909ecf48d1e01f62d1b8fad39f07eb59b8e535aaafe4` remains approved and unchanged; the canonical action's embedded contract equals the local Design contract. Existing Design/Plan lens results are retained as history, with no restart or new PASS. Independent review remains deferred/non-judged, with required lenses owed at EM against unchanged whole-wave baseline `a45aae112bd5b6113292771204ca9ff867bf7ff8`.

Exactly one bounded Taskplane 2.19.0 graph-impact call covered `taskplane/loop.py,taskplane/stage_migration.py`: touched `taskplane`, 33 impacted nodes, `unknown=[]`, depth limit 3, `truncated=true`, `depth_truncated=false`, and requirement-depth policy blocks for `req:R-0001` and `req:R-0003`. Graph fingerprint `0c56ce8259c50abac181e5b9b80414c4187a13788c145000e03c5f885b0be86e` reports scanned HEAD `7538fbacc0bbb3173f926f2a26ff5c7d44882b45`, earlier than the approved expansion candidate; this is bounded cached planning context, not current candidate conformance or complete coverage. No second scan or task expansion masks these limits. The orchestrator must still run the normal mechanical Plan gate and bind the approved amendment while retaining completed Build continuity before T15C dispatch. This Plan amendment makes no real-environment activation, native-success, independent-review, final-sign-off, or publication claim or authorization.

## Approved bounded T11 scope correction

On 2026-09-06 the user explicitly approved adding exactly `taskplane/loop.py` and `taskplane/stage_handoff.py` to T11's existing scope to implement the missing sealed Design-package writer/consumer connection diagnosed in `.taskplane/t11-package-wiring-blocker.md`. T11 owns the complete v2 package writer in `stage_handoff.py` and the package-only Design strategy/quality-authority input and fresh Plan/Build consumption in `loop.py`, using the existing store, validators, authority, and retained-evidence mechanisms. Its existing test file and exact command remain responsible for actual producer-to-consumer positive and independently severed cases. This is an approved mechanical scope correction during ongoing Build; implementation and passing T11 evidence remain to be produced.

All four existing T11 scope entries remain. Every other JSON value and raw task record is preserved, including T11's tests, criteria, contracts, dependencies, type, budget, outcome, and evidence fields; all 24 task IDs and the definitions supporting all 13 completed Build tasks; the T16/T16B split; W01–W34 and all 68 pair selectors; J0–J7; telemetry; native-versus-simulated evidence restrictions; rollback; and human gates. No new module, task, stabilization task, Design restart, or scheduling redesign is introduced. Existing routing and active pins remain unchanged; the new connection stays inactive until HG-E/T15C, and T15C retains sole ownership of activation and shared routing cutover. Historical completed work and accepted foundation evidence remain intact.

The authoritative action reports the Design approved, without errors or staleness, at fingerprint `3b2302b57427c7544cf7909ecf48d1e01f62d1b8fad39f07eb59b8e535aaafe4`; its embedded contract equals the current local Design contract. Exactly one bounded Taskplane 2.19.0 graph-impact read covered `taskplane/loop.py,taskplane/stage_handoff.py`: touched module `taskplane`, 33 impacted nodes, no unknown modules, depth 3, `truncated=true`, `depth_truncated=false`, and R-0001/R-0003 expansion limited by requirement-depth policy. The cached graph was scanned at `223f83440f6010adbcf95403417598a6c09c633e`, so this is bounded planning context, not fresh candidate conformance or complete implementation proof.

The user's Build review-timing override continues: no lens, review, evaluator, code-quality, or test-design workers run for this correction. All existing Plan review/disposition records remain historical; this correction claims no fresh review PASS, gate passage, or completion. The orchestrator alone validates and admits the amended Plan while preserving completed Build history. The preceding T16 sequencing amendment and its evidence statements below remain historical context for that earlier correction.

Status: user-approved bounded sequencing amendment during the existing R-0001 Build, using Taskplane 2.19.0 only. T00–T09, including T01R, retain their exact task bytes and contracts so completed Build evidence is preserved. This amendment changes no Design, requirement, implementation, or tests. The orchestrator owns mechanical admission and preserved Build-history continuation.

Evidence mode: planning amendment only. No new execution result, evaluator PASS, Engineering sign-off, gate passage, or publication is claimed. The previous T16 result at commit `223f83440f6010adbcf95403417598a6c09c633e` remains immutable FAIL history: 41 passed, 67 failed. It is not a passing result to reuse.

## Binding

- Requirement: `R-0001`
- Design content fingerprint: `3b2302b57427c7544cf7909ecf48d1e01f62d1b8fad39f07eb59b8e535aaafe4`
- Design contract SHA-256: `b1b4f95ea32e2a359cfb8dd8af3fe0e886d6725ed8a48271edcbf5e165fb2f0e`
- Design test-strategy fingerprint: `91373377444a82cd6a24618b00435df94e841af4b1fab61a188af33b8b925b22`
- Candidate fingerprint: `80024e6bbe65df8b868bd0c68fbcd8a6d62fcd6bc5c8cee9cf290ed5ec191e60`
- Engine constraint: Taskplane `2.19.0` only.

The former R-0002 Plan and its declared lens route are historical and have no authority here. `plan/tasks.json` intentionally contains no `plan_route`; Taskplane must derive the focused Plan route from canonical settings.

The approved Design embedded in the authoritative action is structurally equal to `design/contract.json`, has no reported errors, and retains the Plan's Design content fingerprint. The SHA-256 above is the historical Plan binding; current local formatting has raw SHA-256 `dc6df4ec264707744a2816113177b46a2182a7ca0aeaf21e250b54cf51045efb`. No Design bytes or bindings are changed by this amendment.

The historical Product-file impact remains in `plan_review.graph_impact`. This amendment made exactly one bounded impact call for `plan/tasks.json`, `plan/plan.md`, and the two T16 test files: 31 impacted nodes, no unknown modules, depth 3, `truncated=true`, `depth_truncated=true`, with R-0001/R-0003 expansion limited by requirement-depth policy. The result is in `sequencing_amendment.graph_impact`; it is bounded planning context, not complete source or implementation validation.

The user explicitly approved separating early policy checks from later full wiring proof while retaining every evidence requirement. Detailed required review is deferred to EM: no lens, code-quality, or test-review workers run during this Build amendment. The four pre-amendment Plan reviews below remain historical and are not refreshed passes for this amendment.

## Outcome

Implement one stateless phase-agent mechanism for Product, Design, Plan, Build, Evaluate, Engineering, and Retro. Phase behavior is data plus a skill under `phase-definition/v1`; the orchestrator remains the only continuation, gate, evaluation-lens, and knowledge-commit authority. The only new module is the thin `taskplane/agent_runtime.py` facade over incumbent dispatch, observation, RunStore, review, and telemetry mechanisms.

Every new producer-consumer edge is proven through the public Taskplane entrypoint using actual upstream producer output. The matching negative holds candidate, sealed input, entrypoint, environment, definitions, and all other edges constant while severing exactly one named edge. Fixture substitution, synthesized envelopes, consumer-fabricated bytes, copied predecessor artifacts, and monkeypatched producers are ineligible.

## Delivery split

### Foundation — S0 to S2

1. T00 freezes current ownership, source, contract, acceptance, and seam traceability.
2. T01 lands closed schemas, canonical serialization, signature lifecycle, and mixed-version fixtures.
3. T01R lands dual readers, unknown-version refusal, and an inactive new-producer capability before any new producer emits.
4. T02 proves host transport or enters explicit degraded observation.
5. T03 implements nonce, key, receipt, consume-ledger, and durable effect-state security.
6. T04 implements the sole declarative phase registry/DAG and default-deny capability declarations.
7. T05 implements the thin shared runtime and capability enforcement.
8. T06 implements governed knowledge proposal/apply, CAS, recovery, retention, and tombstones.
9. T07 implements leases, deadlines, cancellation, retry, fencing, budgets, restart recovery, and historic-format reconciliation.
10. T08 makes minimized telemetry and its verified receipt a sealing prerequisite.
11. T09 composes these mechanisms under the sole orchestrator authority.
12. T16 seals explicit fixture-bypass/provenance policy checks and the real existing W03 registry positive/severed pair after T04 and before T10. Policy-unit observations remain labeled and cannot satisfy production wiring journeys.
13. T10 proves actual available foundation wiring and rollback, consuming the freshly sealed early T16 policy/W03 result. Full future-producer evidence remains mandatory at T16B and T21.

Foundation's HG-F declaration is retained as completed historical gate evidence, including its original J0 wording. The explicit withdrawal above supersedes only that J0 receipt requirement; exact T10 available-foundation positive/severed proof, early T16 policy/W03, candidate, host/degraded evidence, and rollback obligations remain. HG-F does not claim the full future-producer W01–W34 proof or a fresh J0 pass.

### Expansion — S3 to S6

1. T11 adds isolated inactive Product, Design, and Plan adapters.
2. T12 adds the isolated inactive Build adapter with pre-effect revalidation and fencing.
3. T13 adds isolated inactive Evaluate and Engineering adapters with precommitted evaluator selection and append-only judgments.
4. T14 adds the isolated inactive Retro adapter and fully bound single-use publication grant.
5. HG-E revalidates the exact expansion candidate and rollback before activation.
6. T15C alone changes shared selection/composition/compatibility routing and atomically cuts over one phase at a time.
7. T18, after T08, T15C, and T16, proves J7 positive-first and then replays the same public entrypoint with exactly one edge severed.
8. T19 serially preserves J1–J6 with new run, attempt, home, workspace, artifact namespace, and host lease.
9. T16B is the bounded stabilization successor that, after T19 and every producer owner, executes the complete wiring-manifest and fixture-bypass command: all W01–W34 actual positive/severed pairs, every parameter case, and the global attribution aggregate. Missing proof blocks T20.
10. T20 permanently fences the superseded path while preserving evidence and rollback readability.
11. T21 revalidates after retirement, reruns the complete T16B command plus its post-retirement tests, records every parameterized full node ID, and prepares the final evidence ledger.

## Dependency graph and safe waves

```text
Wave 0:  T00
Wave 1:  T01
Wave 2:  T01R
Wave 3:  T02 | T04 | T06
Wave 4:  T03
Wave 5:  T05
Wave 6:  T07
Wave 7:  T08
Wave 8:  T09
Wave 8B: T16, early policy and real W03 proof after T04
Wave 9:  T10, consuming sealed early T16
Gate:    HG-F
Wave 10: T11 | T12 | T13
Wave 11: T14
Gate:    HG-E
Wave 12: T15C, with exact rollback rehearsal before activation
Wave 14: T18, after T08/T15C/T16, new exclusive real-host lease
Wave 15: T19, new exclusive real-host lease
Wave 15B: T16B, full actual-producer W01–W34 proof and aggregate
Wave 16: T20, after complete T16B proof
Wave 17: T21, including full post-retirement wiring and policy revalidation
```

T02's non-emitting discovery may occur during T00. No T02 receipt or event emission may occur before T01 and T01R complete. Parallel tasks own disjoint files. A task that discovers a cross-scope change stops and returns it to the sole scope owner.

## Scope and authority ownership

| Responsibility | Sole implementation owner |
|---|---|
| Contracts, canonical serialization, signing lifecycle | T01 |
| Dual readers and inactive compatibility fixtures | T01R |
| Nonce/key/receipt runtime and effect-state schema | T03 |
| Phase registry, DAG, declarative capability allowlists | T04 |
| Runtime capability enforcement and shared facade | T05 |
| Knowledge apply/CAS/recovery/retention | T06 |
| Lease/retry/deadline/cancellation/budget controller | T07 |
| Telemetry receipt and seal readiness | T08 |
| Continuation, gates, evaluator selection, knowledge commit | T09/orchestrator |
| Product/Design/Plan adapters | T11 |
| Build adapter | T12 |
| Evaluate/Engineering adapters | T13 |
| Retro adapter and publication grant | T14 |
| Shared selector, composition, and compatibility routing | T15C |
| Test provenance and fixture-bypass policy; early W03 contribution | T16 |
| Full W01–W34 actual-producer proof and attribution before retirement | T16B |
| Full wiring and policy rerun after retirement | T21 |

No phase agent or runtime facade may approve, advance, select evaluator lenses, commit knowledge, publish, or create a second registry, lifecycle, retry, storage, or routing authority.

## Compatibility and PR boundaries

1. **PR-1:** T01 and T01R — schemas, signature rules, fixtures, and dual readers. New producers remain inactive.
2. **PR-2:** T02–T05 — host capability, nonce/effect state, registry, and thin runtime. Runtime selection remains inactive.
3. **PR-3:** T06–T10 and T16 — knowledge, lifecycle safety, telemetry sealing, orchestrator composition, provenance policy, foundation proof, and rollback rehearsal.
4. **PR-4:** T11–T14 — phase adapters, all inactive and isolated.
5. **PR-5:** T15C, T18–T19, and T16B — atomic per-phase cutover plus J7, preserved J1–J6 evidence, and full W01–W34 proof before retirement.
6. **PR-6:** T20–T21 — legacy writer shutdown, permanent superseded-path fence, retained readers until sunset, post-retirement validation, and final evidence ledger.

Readers always land before writers. Unknown authority-bearing versions fail closed. A producer cannot become active before every consumer reads its version. Cutover is Product → Design → Plan → Build → Evaluate → Engineering → Retro. Crash before selector CAS leaves the old candidate active; crash after CAS leaves the new candidate active; there is never dual authority. Rollback CAS restores the prior candidate without deleting evidence or rewriting history.

Abort the current slice before further activation on any dual active owner, stale or unknown authority-bearing version, unexplained Design-edge drift, missing required severance, unresolved effect, unavailable required real-host proof, or missing telemetry seal. Preserve immutable evidence, reconcile in-flight operations, and apply only the slice-scoped rollback declared above; expansion rollback never rewrites the accepted foundation.

## Wiring proof

`plan/tasks.json#wiring_manifest` is the machine source for W01–W34. Every row owns these exact parameterized nodes:

- `taskplane/tests/test_r0001_wiring_manifest.py::test_wiring_production_path[Wxx]`
- `taskplane/tests/test_r0001_wiring_manifest.py::test_wiring_severed_edge_fails_closed[Wxx]`

The global selector `taskplane/tests/test_r0001_wiring_manifest.py::test_all_severed_edges_have_attributable_single_edge_failure` rejects any row whose negative changes more than one edge or lacks attributable refusal.

T16 establishes the fixture-bypass/provenance policy and explicitly selects all nine existing policy test functions plus the real `test_wiring_production_path[W03]` and `test_wiring_severed_edge_fails_closed[W03]` nodes. The complete exact command is in its task record; it uses no blanket selection, skip, xfail, or fabricated future output. Its policy-unit refusal tests do not prove future production connections or accept FP-AC04 by themselves.

T16B and T21 must execute `python3 -m pytest -q taskplane/tests/test_r0001_fixture_bypass_detection.py taskplane/tests/test_r0001_wiring_manifest.py` (T21 also includes its post-retirement suite in the same command). This retains every canonical W01–W34 positive/severed selector, all parameter cases, and the aggregate. They fail closed if any row substitutes fixtures, synthesized envelopes, consumer-created bytes, copied artifacts, or monkeypatched producer output for the actual attempt-bound upstream result. Identical bytes pass only with valid production provenance. Missing or unexecutable required runtime proof remains a blocking gap.

The semantic cycle is removed: future owners T13/T14/T15C/T18 depend on T10, so their full proof is scheduled after T19 in T16B. Every manifest owner is an ancestor of T16B; T16B precedes retirement T20, and T21 reruns the full proof after retirement. T16 now also depends on T04 for its actual W03 producer.

## Journey proof

- **J0 — withdrawn history:** T17 and its fresh-install positive/severed selectors are retained only in `withdrawal_amendment.withdrawn_history`. The explicit user withdrawal supersedes this earlier test requirement. J0 remains unproved; no execution, sign-in, replacement test, or PASS is required or authorized for it.
- **J1:** genuine supported-host start, terminal, and collected-output evidence; simulation and degraded observation are ineligible.
- **J2:** actual Design package reaches fresh Plan and Build; removed or altered artifacts fail through the same connection.
- **J3:** owner/reviewer interruption, pickup, cancellation, duplicate-event, and recovery matrix preserves logical identity and uncertain effects.
- **J4:** pass-with-blocker remains blocked; only bounded authorized correction with current evidence progresses.
- **J5:** many-to-many contributions and multiple proofs require separate joint integration evidence.
- **J6:** genuine supported-host Build reaches fresh Evaluate, independent Engineering, human sign-off, Retro, PR outcome, and separately authorized publication without rebuilt bytes.
- **J7:** actual source coverage → decomposition → Plan → seam manifest → Build → conformance runs positive first, then replays the identical public entrypoint with exactly one of its five seams changed.

J7 and J1–J6 use unique run and attempt identities, fresh homes/workspaces/artifact namespaces, and serialized exclusive host leases. One journey's artifacts cannot satisfy another.

## Security and data requirements

T03 uses the operating-system CSPRNG and at least 256 bits of unpredictable nonce material. Nonces and signed receipts bind host, hook, run, phase, operation, attempt, candidate, sealed input, action, expiry, lease, and fence. Private signing keys remain outside the repository, artifacts, logs, inherited environment, and command arguments; OS-backed secret storage is preferred, and any file-backed key requires verified ownership, owner-only permissions, canonical real path, and no symlink. Applicable comparisons use constant-time primitives.

Emergency key disable atomically disables issuance and effects, invalidates unused grants/nonces, fences and reconciles in-flight attempts, rotates the key, preserves historical verification with a compromise marker, and emits an attributable recovery receipt.

Phase capabilities default-deny network egress, inherited environment, dependency acquisition, and undeclared filesystem roots. Exceptions bind exact scheme, host, port, IP policy, redirect policy, DNS policy, variable names, roots, immutable dependency digests, and expiry. The runtime revalidates immediately before an effect and rejects malicious-but-validly-signed skills, SSRF, DNS rebinding, redirect escape, secret-bearing inherited environment, unpinned acquisition, revoked keys, target movement, and symlink swaps.

Knowledge, telemetry, and test artifacts reject secrets, credentials, private prompts/transcripts, traversal, symlink escape, oversize payloads, decompression bombs, and unknown authority fields before staging. Each data class has a retention owner, expiry, and append-only tombstone preserving the relied-on digest and lineage without retaining forbidden content.

Publication grants are single-use and bind subject, candidate, artifact, evidence digest, destination, action, expiry, and expected predecessor. Replay, substitution, destination movement, candidate movement, and predecessor movement fail closed.

Evaluator selection, order, sealed input, and retry policy are committed before dispatch. Every attempt and unfavorable judgment remains append-only and visible; retry cannot suppress a result.

## Lease, retry, telemetry, and budget safety

Durable effect state is `reserved → effect_started → observed|uncertain → terminal → released`. Expired work may be reclaimed only when proven effect-free. A replacement requires terminal-and-released evidence. Uncertain effects reconcile and cannot receive a new nonce or automatic retry. Restart and historical lease formats are reconciled under CAS.

T08 provides actionable W20–W24 alerts:

| Seam | Threshold | Dashboard state | Required action |
|---|---|---|---|
| W20 telemetry | Missing required terminal field or no builder result in 30s | `telemetry_incomplete` | Hold; inspect producer; create no seal |
| W21 receipt/seal | Foreign/stale/unverified receipt or seal wait over 60s | `seal_blocked` | Reconcile identity; prohibit continuation |
| W22 budget | CAS conflict, orphan reservation, or projected use over 90% | `budget_blocked` | Reconcile by operation ID; no dispatch/double charge |
| W23 lease/retry | Heartbeat older than two intervals or lease beyond deadline | `lease_stale` | Reconcile effects; reclaim only if effect-free |
| W24 cancellation | Pending over 60s or uncertainty over five minutes | `cancellation_reconcile` | Fence, preserve evidence, reconcile or request human rescope |

The Plan-wide hard cap is 1,600,000 tokens, 900 actions, 2,400 worker-minutes, 250 MiB evidence, three concurrent workers, and one high-cost worker. One replacement and one correction are allowed per task. The conservative maximum simultaneous reservation is 530,000 tokens, 305 actions, and 960 worker-minutes, including three B3 workers plus release, CAS, hold, seal/final, and rollback reserves. Missing usage is `unavailable`, never zero.

## Human gates

| Gate | Type | Binding |
|---|---|---|
| Product scope | Attributable human | Requirement, repository/run scope, authorized effects |
| Design | Attributable human | Exact Design content and accepted evidence limitations |
| Plan readiness | Mechanical | Plan fingerprint, task graph, contracts, seams, selectors, budgets |
| HG-F | Attributable human; completed historical declaration retained | Foundation candidate, T10 available-foundation proof, sealed early T16 policy/W03, host/degraded receipt, rollback; original J0 receipt wording is historical and superseded only by the explicit withdrawal |
| HG-E | Attributable human | Landed foundation, expansion candidate, current fingerprints, rollback |
| Build | Mechanical | Task contract, scope, outputs, quality evidence |
| Evaluate | Mechanical | Independent evidence completeness |
| Engineering | Attributable human | Requirement-to-implementation sign-off and residual risk |
| Retro | Mechanical | Terminal telemetry receipt and learning |
| Release | Separate attributable human | Candidate, bytes, evidence, destination and publication action |

Human authorization cannot manufacture missing evidence, resolve uncertain effects, waive early T16 or full T16B/T21 proof, substitute for a telemetry seal, or turn degraded observation into real-host proof.

## Historical Plan review

The following pre-amendment review is retained as history. Its results are not a fresh review of the T16 sequencing amendment. On 2026-09-06 the user explicitly deferred detailed required review to EM and prohibited lens, code-quality, and test-review workers during this Build amendment. Mechanical selector conservation and DAG checks are planning checks only, not independent review or evaluator PASS.

For the prior Plan, Taskplane selected exactly four quick Plan lenses: architecture, security, testability, and project-management. Route fingerprint: `74f301f3aebb40bff318765ba9c7bd71bf3ed6ba133e79925f757ea1ddbd19b9`. `plan/tasks.json#plan_review` records the selection rationale, task-to-criterion map, all 26 focused dispositions, graph-impact evidence, and the four historical results.

- **Architecture — pass:** all 13 designed modules, 18 contracts, and 25 proposed edges are covered; the only declared new module is `taskplane/agent_runtime.py`; graph impact reported no unknown module; repeated exact paths are dependency-serialized.
- **Security — pass:** T03, T04, T05, T07, and T14 own nonce/key, default-deny capability, malicious-skill and network/input refusal, pre-effect authority/containment, and single-use publication-grant proof. No implementation scan is claimed: gitleaks, semgrep, dependency-audit, and zizmor are Build-time checks and their executables are not installed in this Plan environment.
- **Testability — pass:** every task has one command string and a bounded timeout; assigned acceptance criteria are copied verbatim; W01-W34 and J0-J7 retain distinct positive/severed identities, isolated evidence modes, and fixture-bypass enforcement. These selectors are planned obligations and were not executed in Plan.
- **Project-management — pass:** T00-T21/T01R/T15C preserve reader-before-writer order, HG-F and HG-E branch gates, exact rollback points, shared-path serialization, successive real-host lease waves, retirement, and post-retirement revalidation.

At that historical review, no selected lens found a substantive Plan blocker after correcting the stale Design fingerprint and replacing paraphrased task criteria with the verbatim assigned R-0001 criteria. Security/data-safety, SRE, integrability, privacy, and cost obligations remain normative in the approved Design and task proof commands even when their Plan dispositions are `not_applicable`.

## Definition of Ready

Plan admission requires:

1. R-0001, the current approved Design, its test strategy, and this Plan share current fingerprints.
2. T00 resolves every logical scope to canonical repository paths and closes bidirectional AC/contract/task/seam traceability without orphans.
3. T01 and T01R precede every new producer; unknown versions and authority fields fail closed.
4. T16 depends on T00, T01, T01R, and T04; its early policy/W03 result is sealed before T10. T10 consumes it with actual available foundation proof; HG-F retains both fingerprints, host/degraded evidence, and rollback; its original J0 receipt wording is historical and superseded only by the explicit withdrawal.
5. Every W01–W34 row has exact actual-producer positive and controlled one-edge-severed selectors.
6. Task scopes within a parallel wave are disjoint and all shared owners are unique.
7. J7 and J1–J6 have isolated identities, environments, namespaces, and real-host leases. J0 remains withdrawn and unproved.
8. Nonce, signing, capability, data-minimization, publication, evaluator, lease, retry, CAS, telemetry, budget, rollback, and retirement policies are explicit.
9. Historical Plan review is preserved without claiming a fresh pass. This user-approved bounded amendment defers detailed required review to EM; no lens, code-quality, or test-review workers run during this Build amendment.
10. The orchestrator mechanically admits the amendment and restores retained completed Build evidence before continuing unfinished tasks; completed T00–T09 task definitions and contracts remain byte-for-byte unchanged.

## Definition of Done

The Plan is realized only when:

1. All 23 active tasks in `plan/tasks.json`, excluding withdrawn T17, complete in the declared DAG and PR order, preserving already completed Build evidence.
2. All 18 Design contracts, 25 Design edges, 21 acceptance criteria, seven active journeys J1–J7, and W01–W34 are covered with no orphan. The sole J0 test/proof withdrawal remains explicit and unproved in the final judgment; whole-Design conformance is not claimed by this amendment.
3. Every W pair holds non-target conditions constant and proves actual producer output plus attributable single-edge failure.
4. Early T16 policy and actual W03 proof pass before T10; HG-F binds them with real available foundation evidence. Full T16B passes after T19 and before T20; T21 reruns and freshly seals every W01–W34 pair, parameter case, fixture-bypass check, and aggregate after retirement.
5. J7 runs positive first and exact one-edge replay second; J1/J6 use genuine supported-host evidence. FP-AC06 remains owned by unchanged T02; withdrawn J0 supplies no acceptance credit.
6. Signature, nonce, key disable, default-deny capability, malicious signed skill, SSRF, DNS rebinding, inherited-secret, unpinned dependency, path-swap, and publication-grant negatives pass with zero unauthorized effects.
7. Knowledge exact replay, changed replay, stale-head CAS, every crash boundary, retention, minimization, and append-only rollback pass.
8. Leases, cancellation, restart, historical formats, fencing, replacement admission, budgets, duplicate charge, telemetry, seal, and actionable recovery pass.
9. Evaluator selection is precommitted; all attempts and unfavorable judgments remain visible.
10. T15C proves atomic cutover and exact rollback with no dual owner; T20 fences the superseded path but retains evidence; T21 revalidates after retirement.
11. Final evidence records every parameterized full node ID, exact case, stable reason, zero-effects assertion, evidence preservation, next action, and rollback readability.
12. Fresh Build, Evaluate, independent Engineering, applicable human sign-off, Retro telemetry, PR-based outcome, and separately authorized publication bind the same tested bytes.

## Current evidence statement

The preceding Plan had Taskplane 2.19.0 Evaluate preparation bound mechanically into each task. This amendment changes only T16/T16B/T10/T20/T21 sequencing references and associated metadata: T16 policy evidence names explicit early policy selectors, while T16B carries the full actual-producer proof command. Each scoped non-test `.py` producer retains its evidence binding and interface classification. T16B scopes only the two existing test files and has no changed implementation producer, so its `evaluation_evidence_edges` and `changed_interfaces` are empty. No classified failure or passing evaluation is invented by the amendment; the previous T16 FAIL remains separately recorded as immutable history.

The four historical quick-lens results, all 26 historical route dispositions, and historical impact remain present. The bounded T16 sequencing amendment adds its own review timing and one bounded graph-impact record. It preserves all W01–W34 selector identities, runtime/native restrictions, telemetry prerequisites, rollback and human gates. Completed T00–T09 evidence remains retained; the earlier T16 FAIL is immutable history. New T16/T16B/T21 execution evidence, full conformance, independent Engineering judgment, and final outcome are not claimed.

The attributable T15C approval in `.taskplane/human-gates/T15C-handoff-owners-0250452.json` appends only `taskplane/stage_handoff.py` and `taskplane/taskplane_lite.py`, in that order, to T15C scope for the existing lifecycle and startup handoff owners. All other Plan JSON values and all 17 completed Build results are retained; the diagnostic FAIL at `02504524a7c52f81b341ca5b1d6aeb64ebf37dbc` remains FAIL. This amendment adds no fresh lens or Engineering judgment.

The approved bounded T15C host-bridge amendment (`.taskplane/human-gates/T15C-host-bridge-ee1862d.json`, exact packet SHA-256 `cce54259986fc45231bac00b02ecdc1d0741f485f5ffb4c237d407f2611e6b80`) appends `taskplane/agent_runtime.py`, `taskplane/tp.py`, `taskplane/producer_observation.py`, and `taskplane/design_host_transport.py` after the unchanged five-path scope prefix. These existing owners connect ordinary `next_action(ws)` dispatch to runtime preparation and completion from authenticated native start/stop/collection events. Candidate `ee1862d97039c56f5cd89027168df8e6c30a9cfd` supplies the reader/startup fixes; it does not prove complete cutover. DQ-02 and approved Design fingerprint `3b2302b57427c7544cf7909ecf48d1e01f62d1b8fad39f07eb59b8e535aaafe4` remain unchanged.

All three conditionally approved adapter connections are necessary and appended in order: `taskplane/build_c.py` (`run_build_phase`, lines 184-315) wraps synchronous runtime execution with pre-effect lease/input/path fencing and terminal reconciliation; `taskplane/review.py` (`run_evaluator_phase`, lines 146-188) precommits the selected pending attempt, rechecks selection at the effect edge, and persists the runtime result; `taskplane/retro.py` (`run_retro_phase`, lines 146-181) rechecks telemetry at launch and publishes its receipt only after accepted runtime completion. Their preparation/completion connections must preserve those checks across asynchronous events without sealing pending work as terminal. No conditional owner is omitted; scope covers only these direct connections, with no unrelated adapter rewrites or new modules, coordinator, store, or lifecycle owner.

Exactly one installed Taskplane 2.19.0 graph-impact call covered all twelve selected scope paths in one comma-separated `--files` value. It reported 32 impacted modules, no unknown modules, local depth 3, contract-only boundaries, contract/requirement depths 1, `truncated=true`, and `depth_truncated=false`; requirement-depth policy blocked `req:R-0001` and `req:R-0003` traversal. Graph fingerprint `e0ec137fce20402c2366955cb91ee65cd9a3cba05bd44a97ed3163f474e9ef96` was scanned at `02504524a7c52f81b341ca5b1d6aeb64ebf37dbc`, older than the current candidate: this is bounded stale coverage, not a complete current scan. No scan repair or repeated discovery ran.

The pre-edit Plan was byte-identical to retained canonical Plan `3607b370feab87a91d5ba02b8575b9db93a57d5e3927d5eefb011ca9cbf8bdaf` (raw SHA-256 `70629948d7e1d46647bfad89f6fd4ad6dbbce372c4117be2ce86bf97d2e9d7b9`). Only T15C.scope changes in the complete 24-task JSON; all selectors, criteria, contracts, edges, dependencies, outcomes, and metadata remain intact. All 17 completed Build records and both historical T15C FAILs remain retained. This Plan-only amendment runs no tests or lenses, grants no live activation/native-success/publication authority, and leaves full-baseline independent review with EM.

## Approved bounded T15C runtime-signing and telemetry clarification

The attributable human approval in `.taskplane/human-gates/T15C-runtime-signing-4d25ecf.json` authorizes the exact purpose-limited correction described in `.taskplane/human-gates/T15C-4d25ecf-approved-runtime-signing-packet.md`, whose verified SHA-256 is `52c520a08bfa607ae6ead262f90ea520fcabc108391337eb6a2ec9ab2a981be9`. It binds run `loop-96e5fc3f7578a04db9da3751`, task T15C, candidate `4d25ecf4fec9b266d71c51ff16f070509ccc9766`, foundation `7538fbacc0bbb3173f926f2a26ff5c7d44882b45`, approved Design `3b2302b57427c7544cf7909ecf48d1e01f62d1b8fad39f07eb59b8e535aaafe4`, and canonical Plan `22f40b0ca44d975b7211b9c47ad7a556b889ac6369493eeb4390e9956aa2788a`. The packet's request language is retained unchanged; the separate attributable decision supplies approval. This is conversation scope authority awaiting normal recovery validation, not an engine-minted or native-host receipt, independent review, or evidence that T15C has completed.

The approved delta is a distinct purpose-limited runtime-receipt signing trust policy under the existing host owner, with trusted-key admission, exact run/candidate/phase/attempt/operation and existing package/definition/knowledge/authority/host/nonce/freshness bindings, validity interval, and disabled-key handling. Trusted key admission must come from that owner and current scoped policy; a payload, key ID, self-consistent digest, or embedded certificate cannot admit its own signing key. Use the existing `stage_handoff.SigningKey` and contract verification rules, including active/retired/revoked/compromised status and disable time, with verification of current validity and freshness before admission or effects. Disabled keys cannot authorize new issuance or effects; preserve the existing emergency-disable, fencing, reconciliation, rotation, and historical-verification obligations. Secrets remain in incumbent host custody under the existing secret-storage and minimization rules. The worker issuer `taskplane_lite._worker_contract_authority` remains exclusively for exact worker lifecycle actions; its secret and permission are not reusable runtime-signing authority. This Plan creates no key or secret.

The minimum connection is the existing host owner supplying the admitted runtime-signing policy to the existing signing/verification boundary, followed by the ordinary shared-routing connection assembling authentic accepted runtime output, nonce custody, the existing attempt ledger, current freshness/trusted-key inputs, and applicable knowledge inputs into the existing `AttemptTelemetryInputs` producers and terminal telemetry prerequisites. The current source at this candidate confirms the boundary: `stage_handoff.SigningKey` requires owner-supplied trust; `_worker_contract_authority` is lifecycle-only; ordinary Retro preparation in `loop.py` refuses without incumbent signed runtime and sealed terminal telemetry inputs; `retro.prepare_retro_phase` and `retro.complete_retro_phase` preserve pre-effect and accepted-completion checks. Connect these existing owners through ordinary producer-to-Retro entry, retaining their authentic output references and exact predecessor lineage. Retro consumes verified telemetry and terminal evidence/metrics receipts; it cannot reconstruct authority from unsigned phase results, diagnostic projections, fabricated nonce/ledger/knowledge records, synthetic terminals, or legacy sealing. Knowledge proposal/apply authority, the runtime facade's non-authoritative role, and the sole orchestrator continuation owner remain as approved.

This clarification stays within T15C's already approved twelve scope paths, in their existing order: `taskplane/stage_migration.py`, `taskplane/tests/test_r0001_phase_cutover.py`, `taskplane/loop.py`, `taskplane/stage_handoff.py`, `taskplane/taskplane_lite.py`, `taskplane/agent_runtime.py`, `taskplane/tp.py`, `taskplane/producer_observation.py`, `taskplane/design_host_transport.py`, `taskplane/build_c.py`, `taskplane/review.py`, and `taskplane/retro.py`. Existing telemetry, nonce, ledger, and knowledge producers are consumed through their current interfaces; this approval creates no new host, coordinator, lifecycle, storage, or telemetry owner and adds no implementation scope. T15C remains the sole shared-routing/cutover owner, with one builder in this task and exact crash-safe rollback and immutable evidence retention. The authorized change is the missing policy and production connection only; all existing task obligations continue unchanged.

Verification must demonstrate the ordinary accepted-output-to-signed-runtime-to-telemetry-to-Retro connection using actual production outputs, followed by independently parameterized negative cases that hold every non-target binding and edge constant. Separately prove missing signing authority or receipt, unadmitted/foreign key or signing purpose, changed run/candidate/phase/attempt/operation or freshness binding, not-yet-valid/expired/stale receipt, disabled key, and each missing/stale/foreign nonce, ledger, knowledge, accepted-output, or terminal-telemetry connection. Each case must produce its own exact selector/case evidence, attributable boundary refusal, preserved evidence, truthful effects, and permitted continuation; one refusal cannot mask another. The existing T15C four-selector command, 360-second timeout, cutover/crash/rollback coverage, and all downstream exact W01–W34, J0–J7, contribution/joint-proof, and finalization obligations remain unchanged. No test is executed or reported passing by this Plan amendment; simulated authority or host checks retain that label and cannot satisfy real-host J0/J1/J6. The retained T15C FAIL and its reported unresolved regression remain adverse history until separately validated, with no inferred whole-suite PASS.

Exactly one installed Taskplane 2.19.0 `graph impact` call used all twelve existing T15C paths in one comma-separated `--files` value. It touched `taskplane` and `taskplane/tests`, reported 32 impacted modules and `unknown=[]`, and retained local depth 3, contract-only boundaries, contract depth 1, and requirement depth 1. It reported `truncated=true`, `depth_truncated=false`, and requirement-depth blocks for `req:R-0001` and `req:R-0003`. Graph content fingerprint `17c1df2b3fdb8d1d258970e76708cd66453c665572b161a080942b9301b191b5` was scanned at `ee1862d97039c56f5cd89027168df8e6c30a9cfd`, before candidate `4d25ecf4fec9b266d71c51ff16f070509ccc9766`. This is bounded stale, truncated planning context, not current complete source coverage or conformance. No scan, repair, repeated impact, or framework audit is authorized by this amendment. The pinned Python reference resolved with the required SHA-256 `cb9e7ebda7fd7ac1ed00ab45d9121957aae55c328b3ee72b74a8aded6aa216b7`.

This append changes only `plan/plan.md`. All bytes of the 24-task `plan/tasks.json` equal `.taskplane/continuity-plan-sources/22f40b0ca44d975b7211b9c47ad7a556b889ac6369493eeb4390e9956aa2788a.json`: raw SHA-256 `614252e0ef12969b666cd4823978e8e39e7eb8a01630c48d19af3a2c31a366e8`, canonical fingerprint `22f40b0ca44d975b7211b9c47ad7a556b889ac6369493eeb4390e9956aa2788a`. Every task, selector, dependency, criterion, contract, metadata value, and scope remains unchanged. The canonical action's approved Design contract equals the local contract; no Design bytes change. All 17 completed Build records, old FAILs, original authenticated root, cumulative usage, and complete EM baseline `a45aae112bd5b6113292771204ca9ff867bf7ff8` remain retained. Under the user's explicit override, this bounded correction runs zero new lenses, reviewers, or subagents and does not restart Design. Independent review stays deferred/non-judged at EM; missing runtime evidence stays missing. No real-environment activation, general traffic, native-success claim, final sign-off, release, or publication is authorized. Normal continuity validation, mechanical gates, and worker lifecycle handling remain with the root orchestrator.

## Withdrawn history — earlier approved bounded T17 native-entry execution clarification

The subsequent explicit user withdrawal above supersedes this entire T17 retry authorization. The following original record is retained as history only; it does not authorize execution, account setup, browser login, or a substitute test.

The renewed human night-mode delegation in `.taskplane/human-gates/night-mode-20260907T044351Z.json` and root's bounded decision in `.taskplane/human-gates/T17-native-entry-retry-83e7416.json` authorize the exact retry packet `.taskplane/human-gates/T17-83e7416-approved-native-entry-retry.md`, verified SHA-256 `383f4d19a7a7a7b70ca2f8362550e05afa6acae64aae4973c22304a5a1651f83`. The decision binds T17 at candidate `83e741610eb2e2edca3b10ccbd895e47571b5e00`, run `loop-96e5fc3f7578a04db9da3751`, approved Design `3b2302b57427c7544cf7909ecf48d1e01f62d1b8fad39f07eb59b8e535aaafe4`, and unchanged canonical Plan `22f40b0ca44d975b7211b9c47ad7a556b889ac6369493eeb4390e9956aa2788a`. It is a delegated execution decision, not a new user message, host receipt, successful test, or Engineering verdict. The approved Design in the complete canonical action equals the local Design contract and is marked approved with no stale binding; this clarification does not restart Design.

Before adding production scope, the bounded T17 retry exercises the incumbent documented entry paths omitted by its first attempt: Codex local-marketplace installation of this exact candidate into an isolated test installation, and Taskplane's public repository/requirement producers followed by `TASKPLANE_STAGE_NATIVE=new-run` entry with current attributable scoped authorization. Local `codex exec` and local marketplace sources are supported interfaces recorded in the approved packet; their availability does not establish J0 success. The existing account's reported ChatGPT authentication does not establish an isolated authenticated host connection, which must come through supported interfaces. Do not copy credentials, prior conversations, cached Taskplane state, receipts, authority, or installed candidate bytes into proof.

The sole scope remains `taskplane/tests/test_r0001_j0_fresh_install.py`. Every byte of `plan/tasks.json` remains unchanged, preserving all 24 tasks and their exact contracts, scopes, tests, selectors, criteria, timeouts, dependencies, edges, and metadata. Governance uses installed Taskplane 2.19.0; isolated tests may load the exact candidate's existing 2.19.0 package only through public installation interfaces. This retry adds no module, transport, coordinator, production-owner edit, implementation scope, broad replan, Design restart, or lens execution. It does not permit version upgrades, changes to the user's active installation or general traffic, extra sidebar tasks, sandbox/hook trust bypass, fabricated identity or seals, inserted producer artifacts, or weakened J0. Any native smoke execution stays within the isolated small test requirement, one host lease at a time, with genuine identity, terminal evidence, and usage, and no publication, credential enrollment, or account mutation.

The first T17 attempt remains FAIL: 13 failed and 1 passed in 196.47 seconds, plus 18 passing affected-area checks. Its Python-only fresh-clone route reached Product without attaching a real host. Candidate-byte verification alone does not prove J0, and the twelve severances remain unverified. Retain all 18 accepted Build contributions, every earlier failed result, the four unresolved T15C compatibility failures, the same authenticated root, all cumulative usage, the 32-hour allowance, and its original start. Full EM baseline remains `a45aae112bd5b6113292771204ca9ff867bf7ff8`. Historical lens dispositions and evidence remain historical; zero new lenses, scans, tests, reviews, or child agents run for this execution-only Plan clarification, and independent review stays deferred/non-judged. If the actual supported host path still cannot complete, report the exact remaining production or external precondition without claiming every alternative impossible. Root owns subsequent scoped decisions, gates, and lifecycle handling.

Exactly one bounded installed Taskplane 2.19.0 graph-impact call used `TASKPLANE_TASK=task_57e59402` and the single `--files` value `taskplane/tests/test_r0001_j0_fresh_install.py`. It touched `taskplane/tests`, returned 32 impacted modules and `unknown=[]`, and retained local depth 3, contract-only boundaries, contract depth 1, and requirement depth 1. Both `truncated` and `depth_truncated` were true; requirement-depth policy blocked further traversal to `req:R-0001` and `req:R-0003`. The graph content fingerprint was `45926667a8516add45e5946e0db192f4ac8167903c7e91dfc6d4dd45af15e25e`, scanned at `4d25ecf4fec9b266d71c51ff16f070509ccc9766`, older than this retry's candidate. This is bounded stale and truncated planning context, not a current complete scan or conformance proof; it grants no additional scope. No scan repair or repeated impact ran. The action-required Python reference's Packaging and DevOps and Security sections were read with the expected whole-file SHA-256 `cb9e7ebda7fd7ac1ed00ab45d9121957aae55c328b3ee72b74a8aded6aa216b7`; their lenses and scanners were not executed.

This append preserves the entire preceding 43,214-byte dirty Plan prefix, SHA-256 `af29a10d49366d8c4a7223aee278653a8fca21f416c49553ae9626ff6e8b37a6`. The 118,153-byte `plan/tasks.json` remains byte-identical to the retained canonical Plan source, raw SHA-256 `614252e0ef12969b666cd4823978e8e39e7eb8a01630c48d19af3a2c31a366e8`. Only `plan/plan.md` is changed by this worker. The inherited explicit-bootstrap/advisory path remains acknowledged without any fabricated native lifecycle binding or worker-authored lifecycle mutation.

## Delegated bounded T19 journey completion amendment

The standing night-mode delegation recorded in `.taskplane/human-gates/T19-journey-completion-scope.json` authorizes packet `.taskplane/human-gates/T19-journey-completion-scope.md`, verified SHA-256 `3206bf24b34aec9667d708252b84179ef4f864dc1d972e659fbfb40be26ba913`. Append only `taskplane/loop.py`, `taskplane/plan_topology.py`, `taskplane/tests/test_r0001_phase_agents_spec.py`, and `agents/spec-phase-definitions.json`, in that order, to T19's six original paths. Connect the already-approved J5 Plan-produced contribution/multiple-proof package to Build and joint acceptance through existing owners; independently sever each required contribution, proof, joint proof and candidate binding through the same entrypoint. The added test path permits only directly affected fixtures/assertions. Registry changes require actual compatibility need; no native transport rewrite, new framework, task or owner is authorized.

Complete the unfinished canonical J2-J4 tests through existing APIs. Subsequent Build must correct the overstrong J3/J4 production-absence diagnosis; missing canonical tests did not establish missing production owners. T19 partial commit `105c926eca43a139b5535c090ff5b70018b106ec` and submission `4020b2f8f6dc7101b757c825218d6b7ea7e85d49453c06ce5ace11965e23b054` remain FAIL: 29 passing checks in 16.15 seconds do not establish full canonical journey coverage. T15C's native nonce-hook route and authenticated CLI exist; the old degraded `prepare_native_entry` diagnostic does not establish general native unavailability. J1/J6 still need root-owned current phase authority/dispatch and genuine observations, actual predecessor packages, and J6 downstream evidence. No fabricated receipts or simulated-to-real promotion can satisfy them. T17/J0 stays withdrawn and unproved, without a fresh-install, blank-account, browser-login or replacement test. T15C regressions and T18's module/file-only simulated-evidence limits remain explicit.

All other T19 fields, every other task and all existing top-level JSON values are preserved, including the exact command, 900-second timeout, criteria, contracts, dependencies, evidence declarations, 23 active tasks and declarations supporting 19 accepted Build results. Only `tasks` and new `t19_journey_completion_amendment` differ from prior canonical Plan `3cb24e59877ad9c92032e239d20f02ada43190ae648ec1cd5fd4403a61277a9a`, raw SHA-256 `e347a9191cde8a886ee0401acc0a890271570eeeb5477c080c87ea2907015061`. The action's approved Design equals the local contract at unchanged fingerprint `3b2302b57427c7544cf7909ecf48d1e01f62d1b8fad39f07eb59b8e535aaafe4`. This append preserves the preceding 57,420 prose bytes, SHA-256 `9a658050b8588d793ae4c2308d8d7a66c2f2233444dfd793c8ab471c2d941070`. Root retains continuity, gates, the original full-wave EM baseline `a45aae112bd5b6113292771204ca9ff867bf7ff8`, authenticated root, usage, counters, allowance and start.

Exactly one installed Taskplane 2.19.0 impact call used `TASKPLANE_TASK=task_db8f4ada` and all ten amended T19 paths in one comma-separated value. It touched `agents`, `taskplane` and `taskplane/tests`, returned 31 impacted modules and `unknown=[]`, with local depth 3, contract-only boundaries and contract/requirement depths 1. It reported `truncated=true`, `depth_truncated=false`, and requirement-depth blocks for R-0001/R-0003. Graph fingerprint `d9a81e1ede71987da71ea7534b21461e43a3f012b0737741e511844a9fb2ffc0` was scanned in module mode at `c63aabc3bb303d7c68e4873ea4368d20a06baca3`, before the current candidate. This stale, bounded result proves no complete current coverage or conformance. No scan, repair, repeat, widening, production edit, test, gate, dashboard or lifecycle receipt was performed. The emitted Python Packaging and DevOps and Security sections were read with verified whole-file hash `cb9e7ebda7fd7ac1ed00ab45d9121957aae55c328b3ee72b74a8aded6aa216b7`. Under the user override, zero new lenses or agents run; required detailed review remains deferred/non-judged, with no fresh PASS or whole-Design conformance claim.

The bounded package-registry decision in `.taskplane/human-gates/T19-package-registry-scope.md` (SHA-256 `59a6b8244c26c5a5ab62fb312b7ed83a5e01199474f6219a67de9947e6c7cf4c`) appends only `scripts/package_openai.py` to T19 scope and adds `t19_package_registry_amendment` to the prior Plan `40e3d6a7d6dc6d5479de99d69504e7313d486b567a30fb0851bbcad89442a56c`. Native run `01a07c45-c497-7470-90d5-541a6e437258` failed before worker dispatch because the produced archive omitted the existing `agents/spec-phase-definitions.json`; preserve its failed record and archive. The repair includes that exact registry and checks its presence and integrity through the existing package producer/validator, preserving historical-release validation contracts and leaving the registry itself unchanged. Clearly labelled supporting checks in the already-owned J1 test file cover actual archive inclusion and missing-registry rejection; both canonical J1 selectors retain their genuine native-evidence requirement, and packaging checks provide no real-host J1 PASS. Every other existing JSON value, all 23 tasks, declarations supporting 19 accepted Build results, the six-file command, 900-second timeout, prior amendments, Design, selectors and evidence modes remain unchanged. J0 stays withdrawn and unproved; original root, start, usage and EM baseline `a45aae112bd5b6113292771204ca9ff867bf7ff8` remain retained. One installed 2.19.0 impact call under `TASKPLANE_TASK=task_c19ab4c6` covered the eleven amended T19 paths, returned 30 impacted modules and no unknown modules, and reported requirement-depth truncation against retained module graph `7926693bb08b9beb46d1fa86ca81501aa4a71323fba3fd22ba4ca6da0b0f8c78` scanned at `105c926eca43a139b5535c090ff5b70018b106ec`; this establishes no current complete coverage. No code, test execution, new lens, subagent, Design approval or Git commit is part of this Plan correction. Review remains deferred/non-judged; root owns gates, continuity and current authority for any subsequent native attempt, with no global installation or publication authorized here.
T19 continues within its existing scope under `.taskplane/human-gates/T19-native-bootstrap-continuation.md` (SHA-256 `33103400327a0b5cd5eea651f7c3d14c10334a00b49c97efbb5700cef70575af`): package fix `fea824dd4c135346d6214547d7400d669c25d320` and its 71-pass result are retained; corrected package `2.19.0+codex.20260907150427` reached production initialization in native session `01a07c69-22eb-7e70-93fa-1b1a70b885f0`, run `j1-native-b6cab32-20260907`, then refused before worker launch because `_phase_bridge_pending` preceded `_stage_bootstrap_pristine_root`. Preserve that failed attempt and correct only this ordering in the already-owned loop/J1 paths, with a labelled supporting production init/next regression protecting pickup, replay, authority rejection and single launch; fabricated state and simulated-to-real evidence promotion remain excluded. Only `t19_native_bootstrap_continuation` is added to Plan JSON; all existing values, 23 tasks, 19 accepted records, the exact command and 900s timeout, prior amendments, Design, canonical selectors and J0 withdrawal remain unchanged. Retain the bounded impact, original root/start/usage, 40h allowance and EM baseline; zero new lenses or Design work, with review deferred/non-judged. Root retains native validation and gates; one builder keeps its contract through candidate validation, with no premature T19 completion or publication.

The delegated packet `.taskplane/human-gates/T19-contract-id-compatibility.md` (SHA-256 `1751eb687b006784ec442f253c9593d0208a4e167d20669e9903e4800a7fe88e`) appends only `taskplane/stage_handoff.py` and `taskplane/stage_entities.py`, in that order, to T19 scope and adds only `t19_contract_id_compatibility_amendment`. Native run `01a07c90-4395-7543-96b0-198b80e9406f` initialized and routed package `2.19.0+codex.20260907154045`, then failed root handoff with `HandoffValidationError: contracts consumed contains an invalid entry`: both validators reject existing namespaced/versioned R-0001 IDs. Preserve exact identities, relation groups and rejection safeguards; validate both owners and normal init-to-next preparation using the already-owned J1 support file. Retain startup commit `77ca46b003b840c113f5bbb49a8cccd404bb60ed`, its 75-pass receipt and all adverse native history; J1/J6 remain unverified. Every other JSON value, all 23 task identities, 19 accepted declarations, criteria, selectors, six-file command, 900s timeout, prior amendments, Design, zero Build lenses, deferred EM review, J0 withdrawal, original root/start/usage/40h allowance and full EM baseline `a45aae112bd5b6113292771204ca9ff867bf7ff8` remain unchanged. One installed 2.19.0 impact call on the exact two paths returned 33 impacted modules, no unknown modules and truncated module-level context scanned at `fea824dd4c135346d6214547d7400d669c25d320`; no graph rebuild or current complete coverage is claimed. Root owns native validation and gates, with the next builder retained through validation before final submission.
