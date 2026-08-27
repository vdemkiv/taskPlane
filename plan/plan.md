# R-0013 Plan — native delivery authority and exact-SHA terminal truth

## Authority, baseline, and bounded impact

This remaining-work Plan is anchored at exact source `feadacd4ac0e6bf485c7f93f64afb9fc90aa21c7` on `codex/r0013-fresh-build-c9ec`, whose latest-main implementation parent remains `c9ec81a021ac74b048bfa58abfbfec870e49711a`. Product spec SHA-256 remains `e8d984c54e0900643f68d13d88d87f6f2fe6659ef84956519627a27afa12b3ed`. The retained approved Design fingerprint is `828833b7a08f47b769c801505706659de6d3e10bc9b39c7831e2a84062ef4dc2`, its approved baseline graph is `78b99071f8ebf44529afe2d70497557d4b280f12f63faf60208927ada466c7f4`, and the current `design/contract.json` has SHA-256 `c218c69533146144017b4dfbb5a701512e774e737c836c3e925c0792fa3019d6`.

The two bootstrap corrections are complete historical prerequisites, not pending tasks or acceptance outcomes. The locator/home fix completed in `2.17.23` at `5f5b49844c99f8a9982b6a4a6308fa04fc5bb5d1`. The distinct W31 native producer/consumer and zero-lens execution-time EM bootstrap completed with independent PASS, was committed and pushed as unreleased `2.17.24` at exact SHA `feadacd4ac0e6bf485c7f93f64afb9fc90aa21c7`, was installed from that exact candidate, and produced the deterministic OpenAI ZIP SHA-256 `d03dd338784124ad20d3cf0df3c90f2b3945d245adb77e219f918b474b4dfdc0`. No remaining task repeats, versions, packages, pushes, installs, or re-proves either bootstrap.

Those completed bootstrap changes preserve the seven Product outcomes and nine Product contract ids, add no remaining graph task or Design edge, and do not authorize Product or Design replay. The remaining tasks inherit the approved graph and retain local depth 3, contract-only boundary, contract depth 1, and requirement depth 1.

## Completed bootstrap boundary

The completed locator half binds native-hook and repository-bridge execution in both root and Taskplane task worktrees to the canonical home selected by the secure checkout locator. A missing locator, a non-canonical locator home, a task-worktree locator that does not bind the root run/home, or an inherited home that disagrees with the locator fails closed before any event, capability, observation, or receipt write. An explicit secure locator that selects canonical `$HOME/.taskplane` remains valid; unauthorized fallback or mismatch does not. The generated launcher and host runtime preserve the locator-bound home, and receipt copying remains prohibited as implementation, fixture, fallback, or acceptance evidence.

The completed producer half closes the observed production gap without creating Taskplane lifecycle authority. On a genuine Codex `SubagentStop`, the hook observes the evaluator or execution-time EM result's exact bytes and binds exactly one external event plus one-use capability to the current run, task, lowercase stage, emitted producer identity, Codex session, Codex turn, canonical result path, output schema id, output-contract fingerprint, and exact source SHA. Zero-lens submission locates and consumes exactly one fresh matching observation before collection; missing, stale, ambiguous, mismatched, replayed, caller-authored, copied, or synthetic material fails closed before guidance, collection, submission, or gate success. The production review seam accepts sealed zero-lens delivery authority for execution-time EM, bypasses `automatic_sweep_route`, and produces exactly zero lens slots; legacy EM without that authority remains outside this correction. Duplicate native/bridge delivery may replay protocol disposition only and never mints a second event or capability. This remains a host evidence producer/consumer adapter, not a scheduler, worker registry, lifecycle journal, queue, or general W31/cold-start redesign.

The independently passing bootstrap evidence is retained only as the historical readiness basis for this Plan. It is not assigned to T01–T09, does not satisfy AC1–AC7 by itself, and creates no eighth acceptance outcome.

## Delivery DAG and receipts

There are exactly nine remaining tasks in three delivery waves. Wave 1 offers T01–T07, the seven pairwise-disjoint leaf-readiness owners, as one native set with no pending prerequisite task. Wave 2 is the sole shared-adapter integration owner after all seven leaf receipts. Wave 3 is AC7 end-to-end closure after the integration receipt.

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

The seven Wave-1 leaf scopes are pairwise disjoint and have no task dependencies. T01, T03, T04, and T06 retain leaf-only selectors; shared and live-path R-0013 selectors remain exclusively at T08. No task owns host spawn, scheduling, capacity, reservation, admission, replay, lease-concurrency, worker lifecycle, event queue, or execution DAG. `delivery_mode=build` and `automatic_lenses=[]`; Build, Fix, Evaluate, and execution-time EM use zero Taskplane lens workers. T02 validates retained Design-only evidence without dispatching lenses or repeating the completed all-26 sweep.

## Seven outcomes, four P0 controls, and all 21 pair classifications

The unique criterion set remains exactly AC1–AC7, copied verbatim into `tasks.json`. The completed bootstrap is readiness evidence only and is not an acceptance owner. T03 owns the execution zero-lens policy; AC7 stays assigned to T07/T09, with T08 carrying only its integration re-attestation. The only P0 controls remain P0-1 atomic exact-SHA finalization (AC7), P0-2 native budget telemetry and enforcement (AC6), P0-3 host-native dispatch authority (AC1 and AC4), and P0-4 real-checkout wiring closure (AC7).

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

The task-set contract union remains exactly the nine canonical Product ids: `contract:design.codex-native-capability-inventory`, `contract:design.quick-concurrent-all-lens-sweep`, `contract:delivery.execution-zero-lens`, `contract:delivery.codex-native-dispatch`, `contract:delivery.event-driven-wait`, `contract:delivery.acceptance-wave-ceiling`, `contract:delivery.bounded-stage-handoff`, `contract:delivery.exact-sha-terminal-truth`, and `resource:exports.exact-sha-terminal-truth`. The completed bootstrap added or renamed no Product contract.

The retained terminal inventory still covers all 11 approved proposed modules and the previously planned `exports/terminal` projection. T09 retains the exact 24 approved edge strings, and every remaining task retains local depth 3, contract-only boundary, contract depth 1, and requirement depth 1.

## Risks, budgets, exclusions, and stop conditions

The completed bootstrap is immutable baseline evidence, not permission to substitute policy coverage, test doubles, caller-authored material, or copied receipts for production evidence during delivery. Any regression in locator binding, exact result-byte observation, one-use capability consumption, replay refusal, or execution-time EM zero-lens authority blocks the affected remaining task; it does not reopen bootstrap or authorize version/install work. Remaining containment is seven disjoint leaf receipts, one exclusive adapter integrator, AC7 only after integration, registered exact checkouts, mutation proofs, an orchestrator-only terminal capability, exactly eight immutable projections, and one bounded CAS reconciliation head.

Before every native start, elapsed time, unique sessions, total tokens, and uncached input must be finite, non-negative, and strictly below 28,800 seconds, 60 sessions, 150,000,000 tokens, and 25,000,000 uncached tokens. Equality, breach, missing usage, a handoff at or above 4,000 tokens, silent transport deadline, or a second failed Fix/Evaluate cycle stops for attributed human scope or architecture review. The stopped set is never silently resumed or replaced.

AC7 final truth is exactly eight projections—Git HEAD, governed progress, run journal, tasks/gates, public report, repository verification, release evidence, and redacted exports terminal evidence—sharing one SHA/status/requirement/fingerprint set. No individual projection, opaque fingerprint, generated temporary checkout, or foreign receipt has authority. A dirty/wrong SHA, missing selector, failed severed-edge proof, CAS fork/gap/collision, executing retained run, partial projection, cleanup before reconciliation, or SHA-changing merge blocks Done and every delivery claim.

This revision admits only the remaining T01–T09 delivery graph. It does not admit bootstrap replay, version/package/install work, broader W31/cold-start redesign, history repair, P1/P2 follow-up, R-0011, unrelated R-0013 backlog, completed R-0001 replay, scheduler/capacity/reservation/admission/replay/lease/lifecycle machinery, caller-authored or copied receipts, or another lens sweep. Merge to main, tag, public Marketplace publication, release declaration, credentials for remote mutation, and origin changes remain excluded. Plan authoring runs no product tests or implementation. Volodymyr Demkiv explicitly authorized this direct Plan finalization to bypass only the old run's immutable 8-hour ledger; that authority does not change the acceptance criteria, contracts, Design, task scopes, delivery gates, or execution bounds.
