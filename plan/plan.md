# R-0013 Plan — native delivery authority and exact-SHA terminal truth

## Authority, baseline, and bounded impact

This unchanged Plan is provenance-re-attested to exact clean source `d6b2cd9ca98d2a782748ef82afd831690cdd6a78`, Product spec SHA-256 `e8d984c54e0900643f68d13d88d87f6f2fe6659ef84956519627a27afa12b3ed`, approved Design fingerprint `828833b7a08f47b769c801505706659de6d3e10bc9b39c7831e2a84062ef4dc2`, and approved baseline graph `78b99071f8ebf44529afe2d70497557d4b280f12f63faf60208927ada466c7f4`. The canonical Design object in the engine payload and `design/contract.json` has identical canonical SHA-256 `d7e696dc8b4fb0eca6ae687a7db308346a77aa889a4eea23bdf1687abb2f7404`. The re-anchor changes provenance only: the previously approved tasks, waves, scopes, tests, criteria, dependencies, contracts, Design edges, depth policy, native-authority restrictions, and bounded impact inventory remain unchanged. The approved dirty Design re-attestation is governing input, not Plan-owned work.

The single bounded impact derivation covered the exact seven leaf scopes, the shared `loop.py`/`tp.py` integration seam, and `exports/terminal/r0013`. It touched `taskplane`, `taskplane/tests`, and `exports/terminal`; found 27 impacted modules; had no truncation, policy block, or degraded scanner; and retained the approved local-depth-3, contract-only, contract-depth-1, requirement-depth-1 policy. The impact graph fingerprint is `c8b7ce1f643cfe1d4e7c5a0f63d407591b2a4d54e487f6e376dfce9200b3057c`, and scan-quality fingerprint is `b8bc84a6028454cc358b4c1757a0161d5a144f80a4f215c0f1a4b30902c2e27a`. Its sole unknown, `exports/terminal`, is the approved new repository projection identity and is declared explicitly; it does not widen production scope.

## Delivery DAG and receipts

There are nine tasks in three waves. Wave 1 contains seven pairwise-disjoint leaf-readiness owners and must be offered as one native set without Taskplane host-capacity truncation. The observed 13-slot host envelope is evidence only, never a Plan constant. Wave 2 is the sole shared-adapter integration owner and starts only after all seven `taskplane.acceptance-leaf-readiness/v1` receipts are green. Wave 3 performs AC7 end-to-end real-checkout and atomic-finalization acceptance only after `taskplane.integration-readiness/v1` exists.

| Wave | Task | Exclusive owner | Receipt / barrier |
| --- | --- | --- | --- |
| 1 | T01 AC1 | Native authority validator and exact tests | `leaf-ready:AC1` |
| 1 | T02 AC2 | Design-sweep validator and exact tests; validates the retained one-time Design evidence and never runs another lens sweep | `leaf-ready:AC2` |
| 1 | T03 AC3 | Zero-lens execution adapters and exact tests | `leaf-ready:AC3` |
| 1 | T04 AC4 | Sealed ready set, BUILD-C consumption, native intent and wait adapters | `leaf-ready:AC4` |
| 1 | T05 AC5 | Seven-outcome ceiling and exhaustive pair-map validation | `leaf-ready:AC5` |
| 1 | T06 AC6 | Delta handoff, native usage and hard budget screen | `leaf-ready:AC6` |
| 1 | T07 AC7 | Pure wiring/finalization APIs and focused fault/refusal selectors, not end-to-end acceptance | `leaf-ready:AC7` |
| 2 | T08 | Only `taskplane/loop.py` and `taskplane/tp.py`; remove stage-native execution authority and connect the seven sealed APIs | `integration-ready:r0013`; requires T01–T07 |
| 3 | T09 AC7 | Exact pinned/final checkout proofs, 21 producer-edge mutations, eight-surface reconciliation, full suite, and redacted terminal projection | `acceptance-complete:AC7`; requires T08 |

No leaf writes `loop.py` or `tp.py`, no two same-wave scopes overlap, and no task owns host spawn, scheduling, capacity, reservation, admission, replay, lease-concurrency, lifecycle, event queue, or execution DAG. `delivery_mode=build` and `automatic_lenses=[]`; there are zero per-task or automatic Build lenses. T02 implements and tests the Design-only validator against already-approved evidence—it does not dispatch or repeat the completed all-26 sweep.

## Seven outcomes, four P0 controls, and all 21 pair classifications

The unique criterion set is exactly AC1–AC7, copied verbatim into `tasks.json`; duplicated ownership on T08/T09 is integration or terminal re-attestation, not an eighth outcome. The only P0 controls are: P0-1 atomic exact-SHA finalization (AC7), P0-2 native budget telemetry and enforcement (AC6), P0-3 host-native dispatch authority (AC1 and AC4), and P0-4 real-checkout wiring closure (AC7).

The Design's exact outcome-level pair policy remains authoritative. A serialized row is an acceptance/evidence dependency, not permission to idle disjoint leaf implementation: all seven leaf owners may build in Wave 1, while T08 and T09 enforce the named receipt barriers before the dependent acceptance can close.

| Pair | Classification | Available wave / exact reason |
| --- | --- | --- |
| AC1–AC2 | parallel | `leaf-wave-1`: disjoint native-authority and Design-sweep owners |
| AC1–AC3 | parallel | `leaf-wave-1`: disjoint native-authority and zero-lens owners |
| AC1–AC4 | serialized | AC4 adapter integration consumes the AC1 authority map and removed stage edge |
| AC1–AC5 | parallel | `leaf-wave-1`: disjoint native-authority and Plan-boundary owners |
| AC1–AC6 | parallel | `leaf-wave-1`: disjoint native-authority and budget owners |
| AC1–AC7 | serialized | AC7 finalization consumes the AC1 native-authority receipt |
| AC2–AC3 | parallel | `leaf-wave-1`: disjoint Design-sweep and zero-lens owners |
| AC2–AC4 | parallel | `leaf-wave-1`: Design-only sweep validation is independent of native dispatch leaf code |
| AC2–AC5 | parallel | `leaf-wave-1`: disjoint Design-sweep and Plan-boundary owners |
| AC2–AC6 | parallel | `leaf-wave-1`: disjoint Design-sweep and budget owners |
| AC2–AC7 | serialized | terminal acceptance closes only after the Design-sweep receipt is dispositioned |
| AC3–AC4 | serialized | AC4 native briefs consume AC3 zero-lens execution authorization at the shared loop adapter |
| AC3–AC5 | parallel | `leaf-wave-1`: disjoint zero-lens and Plan-boundary owners |
| AC3–AC6 | parallel | `leaf-wave-1`: disjoint zero-lens and budget owners |
| AC3–AC7 | serialized | AC7 finalization consumes execution zero-lens receipt and trace |
| AC4–AC5 | serialized | AC4 ready-set emission consumes AC5 exhaustive Plan pair map |
| AC4–AC6 | serialized | AC4 native start screen consumes AC6 non-null usage and budget decision |
| AC4–AC7 | serialized | AC7 finalization consumes native dispatch and wait receipts |
| AC5–AC6 | parallel | `leaf-wave-1`: disjoint Plan-boundary and budget owners |
| AC5–AC7 | serialized | AC7 finalization consumes exact seven-outcome Plan receipt |
| AC6–AC7 | serialized | AC7 finalization consumes complete native usage and bounded-handoff receipts |

## Contract, module, edge, and depth coverage

The task-set contract union is exactly the nine canonical Product ids: `contract:design.codex-native-capability-inventory`, `contract:design.quick-concurrent-all-lens-sweep`, `contract:delivery.execution-zero-lens`, `contract:delivery.codex-native-dispatch`, `contract:delivery.event-driven-wait`, `contract:delivery.acceptance-wave-ceiling`, `contract:delivery.bounded-stage-handoff`, `contract:delivery.exact-sha-terminal-truth`, and `resource:exports.exact-sha-terminal-truth`.

The terminal inventory covers all 11 approved proposed modules—`.codex`, `design`, `exports`, `lenses`, `plan`, `skills/tp-go`, `taskplane`, `taskplane/tests`, `taskplane/native_authority.py`, `taskplane/design_sweep.py`, and `taskplane/terminal_truth.py`—plus the impact-required exact unknown `exports/terminal`. This inventory does not grant write scope: T09 remains confined to `exports/terminal/r0013/**`. T09 also carries the exact 24 approved proposed-edge strings for final real-checkout and severed-edge closure. Every task carries the unchanged depth policy: local 3, contract-only boundary, contract 1, requirement 1.

## Risks, budgets, exclusions, and stop conditions

The highest risks are accidentally retaining renamed stage-native execution authority, treating a leaf receipt as completed AC7, accepting generated/foreign checkout proof, or exposing partial terminal projections as authority. The containment is structural: static plus behavioral authority tests, seven disjoint leaf receipts, one exclusive adapter integrator, AC7 E2E only after the integration receipt, registered pinned/final Git checkouts, one-edge mutation proofs, an orchestrator-only terminal capability, exactly eight immutable projections, and one CAS head with bounded idempotent reconciliation.

Before every native start, elapsed time, unique sessions, total tokens, and uncached input must be finite, non-negative, and strictly below 28,800 seconds, 60 sessions, 150,000,000 tokens, and 25,000,000 uncached tokens. Equality, breach, missing usage, a handoff at or above 4,000 tokens, silent transport deadline, or a second failed Fix/Evaluate cycle stops for attributed human scope or architecture review. The stopped set is never silently resumed or replaced.

AC7 final truth is exactly eight projections—Git HEAD, governed progress, run journal, tasks/gates, public report, repository verification, release evidence, and redacted exports terminal evidence—sharing one SHA/status/requirement/fingerprint set. No individual projection, opaque fingerprint, generated temporary checkout, or foreign receipt has authority. A dirty/wrong SHA, missing selector, failed severed-edge proof, CAS fork/gap/collision, executing retained run, partial projection, cleanup before reconciliation, or SHA-changing merge blocks Done and every delivery claim.

This Plan contains no W31 or cold-start work, history/tag repair, P1/P2 follow-up, R-0011, unrelated R-0013 backlog, completed R-0001 replay, CI/package/manifest/docs/release work, scheduler/capacity/reservation/replay machinery, automatic lens sweep, push, tag, publication, credential use, release, or origin mutation. Plan authoring runs no product tests and makes no implementation change. The orchestrator alone may validate and gate this Plan; the planner neither submits, approves, nor advances the loop.
