# R-0013 Plan — native delivery authority and exact-SHA terminal truth

## Authority, baseline, and bounded impact

This bounded Plan action is anchored at exact source `0b3859a1c39be8aa6ae2d1fc342454e716a6e1cf` on `codex/r0013-fresh-build-c9ec`, whose latest-main implementation parent remains `c9ec81a021ac74b048bfa58abfbfec870e49711a`. Product spec SHA-256 remains `e8d984c54e0900643f68d13d88d87f6f2fe6659ef84956519627a27afa12b3ed`. The retained approved Design fingerprint is `828833b7a08f47b769c801505706659de6d3e10bc9b39c7831e2a84062ef4dc2`, its approved baseline graph is `78b99071f8ebf44529afe2d70497557d4b280f12f63faf60208927ada466c7f4`, and `design/contract.json` has canonical SHA-256 `d7e696dc8b4fb0eca6ae687a7db308346a77aa889a4eea23bdf1687abb2f7404`.

Volodymyr Demkiv explicitly clarified the original delivery authority: repair the bootstrap defect first, bump the fixed plugin from `2.17.22` to `2.17.23`, build and push that version, install the exact built version locally, prove genuine native-host operation in the dedicated R-0013 home, and only then continue the remaining R-0013 Build. That attributed decision narrowly overrides the stale R-0013 out-of-scope statements that excluded the bootstrap/version/package/branch-push boundary. It does not change the seven Product outcomes, add a contract id, replay Product or Design, or authorize a tag, public Marketplace publication, release declaration, `origin/main` mutation, or any other remote mutation.

The exactly one bounded impact derivation covered the locator/hook sources, synchronized Codex and Claude manifests, compatibility and release-evidence surfaces, OpenAI packager, public version records, and their affected tests. It touched `(root)`, `.claude-plugin`, `.codex`, `.codex-plugin`, `design`, `design/schemas`, `hooks`, `scripts`, `taskplane`, and `taskplane/tests`; found 24 impacted nodes including `req:R-0013`; had no truncation, policy block, or degraded scanner; and returned `(root)` as the exact unknown module. The generated `.taskplane` launcher module remains carried from the prior bounded bootstrap impact because it is an explicit T00 runtime surface. The impact graph fingerprint is `cc5b8a81f8428d116906fe1c602d664cde97288f807f188c078ae67e35a05bee`, scan-quality fingerprint is `21be6bde277516eafe5606ddc825f092a16874e970b448241362d7d8ce161c75`, and scanned HEAD is `0b3859a1c39be8aa6ae2d1fc342454e716a6e1cf`. The policy remains local depth 3, contract-only boundary, contract depth 1, and requirement depth 1.

## Authorized T00 correction and release boundary

T00 is the serialized prerequisite and is not complete merely when source tests pass. Its implementation must bind both native-hook and repository-bridge execution to the canonical home resolved by the secure checkout locator. A missing locator, a non-canonical locator home, or an inherited home that disagrees with the locator must fail closed before any receipt write. An explicit secure locator that selects canonical `$HOME/.taskplane` is valid; the defect is unauthorized fallback or mismatch, not that canonical path itself. The generated launcher and host runtime must preserve the locator-bound home, and receipt copying is prohibited as implementation, fixture, fallback, or acceptance evidence.

The same T00 task commit must synchronize `2.17.23` across the authoritative Codex and Claude manifests, Marketplace metadata, runtime release evidence, compatibility/schema/CI assertions, README and CHANGELOG; keep the inherited historical versions as history rather than rewriting them; and keep its shell-free declared pytest suite as the engine gate. `python3 scripts/ci_evals.py --verify-release-surface` and `python3 scripts/package_openai.py` remain separate mandatory orchestrator evidence before T00 completion. The independently evaluated task SHA then crosses a root-owned verification boundary: push only `codex/r0013-fresh-build-c9ec`, prove the remote branch tip is that exact SHA, install the built plugin at `/Users/vdemkiv/.codex/plugins/cache/taskplane-marketplace/taskplane/2.17.23`, and verify its authoritative manifest declares exactly `2.17.23`.

Finally, without copied receipts, a fresh exact-name Codex native subagent must generate a current compatible SubagentStart/SubagentStop lifecycle in this checkout/run. The evidence must prove `native_effective`, managed policy `permitted`, a loaded session, one stable fresh native identity across start and stop, the current checkout identity, run `a8308709d819466c9de50f63b2f90226`, and the locator-bound `/Users/vdemkiv/.taskplane-r0013` home. No unauthorized default-home receipt write may occur. Only one `bootstrap-home-ready` receipt containing the implementation SHA, test/package digests, exact pushed ref, installed-manifest digest, and live-host receipt fingerprint may pass T00 and make T01–T07 ready.

## Delivery DAG and receipts

There are exactly ten tasks: one serialized bootstrap/version prerequisite followed by the unchanged three-wave R-0013 implementation, integration, and closure graph. T00 owns the source, version, package, branch-push, local-install, and genuine-host proof barrier described above. After its independent gate passes, Wave 1 offers the seven pairwise-disjoint leaf-readiness owners as one native set. Wave 2 remains the sole shared-adapter integration owner after all seven leaf receipts, and Wave 3 remains AC7 end-to-end closure after the integration receipt.

| Wave | Task | Exclusive owner | Receipt / barrier |
| --- | --- | --- | --- |
| Prerequisite | T00 bootstrap/version | Locator-bound home propagation; `2.17.23` synchronized version surfaces; affected regressions; deterministic OpenAI package; exact branch push; exact local install; genuine fresh native lifecycle proof | `bootstrap-home-ready`; all source, package, pushed-SHA, installed-manifest, and live-host evidence must agree |
| 1 | T01 AC1 | Native authority validator and exact tests | `leaf-ready:AC1` |
| 1 | T02 AC2 | Design-sweep validator and exact tests; validates the retained one-time Design evidence and never runs another lens sweep | `leaf-ready:AC2` |
| 1 | T03 AC3 | Zero-lens execution adapters and exact tests | `leaf-ready:AC3` |
| 1 | T04 AC4 | Sealed ready set, BUILD-C consumption, native intent and wait adapters | `leaf-ready:AC4` |
| 1 | T05 AC5 | Seven-outcome ceiling and exhaustive pair-map validation | `leaf-ready:AC5` |
| 1 | T06 AC6 | Delta handoff, native usage and hard budget screen | `leaf-ready:AC6` |
| 1 | T07 AC7 | Pure wiring/finalization APIs and focused fault/refusal selectors, not end-to-end acceptance | `leaf-ready:AC7` |
| 2 | T08 | Only `taskplane/loop.py` and `taskplane/tp.py`; remove stage-native execution authority and connect the seven sealed APIs | `integration-ready:r0013`; requires T01–T07 |
| 3 | T09 AC7 | Exact pinned/final checkout proofs, 21 producer-edge mutations, eight-surface reconciliation, full suite, and redacted terminal projection | `acceptance-complete:AC7`; requires T08 |

T00 and T08 both touch `taskplane/tp.py`, and T00 and T07 both touch `taskplane/release_evidence.py`; both overlaps are serialized because every leaf depends on T00. The seven Wave-1 leaf scopes remain pairwise disjoint, and no two same-wave scopes overlap. T01, T03, T04, and T06 retain leaf-only selectors; shared and live-path selectors remain exclusively at T08 so leaf Evaluate never depends on downstream integration. No task owns host spawn, scheduling, capacity, reservation, admission, replay, lease-concurrency, lifecycle, event queue, or execution DAG. `delivery_mode=build` and `automatic_lenses=[]`; there are zero per-task or automatic Build, Fix, Evaluate, or execution-time EM lens workers. T02 validates the retained Design-only evidence and never dispatches or repeats the completed all-26 sweep.

## Seven outcomes, four P0 controls, and all 21 pair classifications

The unique criterion set remains exactly AC1–AC7, copied verbatim into `tasks.json`; T00 re-attests AC1, AC4, and AC7 as a prerequisite and creates no eighth outcome. Duplicated ownership on T08/T09 remains integration or terminal re-attestation. The only P0 controls remain P0-1 atomic exact-SHA finalization (AC7), P0-2 native budget telemetry and enforcement (AC6), P0-3 host-native dispatch authority (AC1 and AC4), and P0-4 real-checkout wiring closure (AC7).

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

The task-set contract union remains exactly the nine canonical Product ids: `contract:design.codex-native-capability-inventory`, `contract:design.quick-concurrent-all-lens-sweep`, `contract:delivery.execution-zero-lens`, `contract:delivery.codex-native-dispatch`, `contract:delivery.event-driven-wait`, `contract:delivery.acceptance-wave-ceiling`, `contract:delivery.bounded-stage-handoff`, `contract:delivery.exact-sha-terminal-truth`, and `resource:exports.exact-sha-terminal-truth`. T00 traces only the exact existing `contract:delivery.codex-native-dispatch` lifecycle-observation boundary. Volodymyr Demkiv's attributed narrow correction supplies authority for the locator/version/package/install prerequisite without adding or renaming a Product contract.

The retained terminal inventory still covers all 11 approved proposed modules and the previously planned `exports/terminal` projection. T00 declares the generated `.taskplane` module carried by the prior bootstrap impact and the current impact tool's exact unknown `(root)`; `hooks`, `scripts`, the plugin manifests, `design`, and `taskplane` are existing graph surfaces. T00 intentionally invents no Design edge. T09 retains the exact 24 already-approved edge strings, and every task retains the depth policy of local 3, contract-only boundary, contract 1, and requirement 1.

## Risks, budgets, exclusions, and stop conditions

The highest immediate risk is confusing a copied receipt with a repaired producer path. The bootstrap task must derive one canonical home from the secure locator, propagate it through native and repository hooks and the generated launcher, fail before write on absence or mismatch, prove the explicit canonical-default locator case remains valid, and prove the dedicated R-0013 path writes nothing to the default home. A copied/stale receipt, version drift, nondeterministic or mismatched package, remote-tip mismatch, installed-manifest mismatch, missing genuine receipt, cross-run identity inheritance, unauthorized scope drift, renamed stage-native authority, generated/foreign checkout proof, or partial terminal truth stops the corresponding gate. After T00 and its branch boundary, containment remains seven disjoint leaf receipts, one exclusive adapter integrator, AC7 only after integration, registered exact checkouts, mutation proofs, an orchestrator-only terminal capability, exactly eight immutable projections, and one bounded CAS reconciliation head.

Before every native start, elapsed time, unique sessions, total tokens, and uncached input must be finite, non-negative, and strictly below 28,800 seconds, 60 sessions, 150,000,000 tokens, and 25,000,000 uncached tokens. Equality, breach, missing usage, a handoff at or above 4,000 tokens, silent transport deadline, or a second failed Fix/Evaluate cycle stops for attributed human scope or architecture review. The stopped set is never silently resumed or replaced.

AC7 final truth is exactly eight projections—Git HEAD, governed progress, run journal, tasks/gates, public report, repository verification, release evidence, and redacted exports terminal evidence—sharing one SHA/status/requirement/fingerprint set. No individual projection, opaque fingerprint, generated temporary checkout, or foreign receipt has authority. A dirty/wrong SHA, missing selector, failed severed-edge proof, CAS fork/gap/collision, executing retained run, partial projection, cleanup before reconciliation, or SHA-changing merge blocks Done and every delivery claim.

This revision admits only the human-authorized locator-bound bootstrap repair, `2.17.23` patch synchronization, deterministic OpenAI package, current-branch push, exact local install, and genuine-host proof prerequisite. It does not admit other W31/cold-start work, history repair, P1/P2 follow-up, R-0011, unrelated R-0013 backlog, completed R-0001 replay, scheduler/capacity/reservation/replay machinery, or another lens sweep. The only authorized remote boundary is root's push of the independently green T00 candidate to `codex/r0013-fresh-build-c9ec`; merge to main, tag, public Marketplace publication, release declaration, credentials for any other mutation, and all other origin changes remain excluded. Plan authoring runs no product tests or implementation. The orchestrator may submit this revision to the mechanical Plan gate, but no T00 implementation dispatch occurs before the validated Plan is human-approved, and no remaining R-0013 dispatch occurs before the complete `bootstrap-home-ready` evidence passes.
