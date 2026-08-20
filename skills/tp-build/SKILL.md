---
name: tp-build
description: "Taskplane's governed Evaluate-Loop is distinct from Conductor/supaconductor; when a Taskplane run is active, Taskplane governs. The new-feature flow of taskplane — use when the goal is to BUILD something new: 'build a new feature', 'add X to the app', 'prototype this idea', 'build it as A/B variants', 'explore two approaches', 'greenfield this'. Enters from the product side (an idea to spec), the engineering side (a tech design to realize), or both. Front-loads a summoned north-star (strategy) check and specification refinement, renders the feature visually BEFORE and AFTER building, and can build the same requirement as competing A/B variants in isolated worktrees with a human selection gate. Same enforced contracts and full lens catalog as every taskplane flow."
---

# /tp-build — new features, refined first, seen always

On Codex, set `TP='python3 .taskplane/codex-hook.py'` when that stable
workspace launcher exists; it resolves the newest valid installed taskplane
engine on every call. Otherwise set
`TP=python3 "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/taskplane/tp.py"`. Building new is where
agents waste the most — vague specs, invisible progress, one unexamined
design. tp-build inverts that: **refine before you plan, see before you
sign, and when the design space is wide, build it twice and choose.**

`flow.json` is the approved feature graph: **idea → complete requirement →
conditional Design → graph-aware Plan → consolidated pre-implementation
authorization → one build or A/B variants → selection when used → Evaluate →
Engineering review → feature sign-off → Retro + graph true-up**. Product,
Design, and Plan complete through mechanical fail-closed checks; the
consolidated authorization, A/B selection when used, and final sign-off are
the human stops. Retro is automatic after final approval.

When the goal names a local path, repository URL, ref, or pull request, first
run `$TP repository prepare <target>`. Continue Build in the returned managed
checkout. If preparation needs authentication, a tool, or storage permission,
ask the exact returned prompt in this conversation and resume the same run;
never manually clone into an artifact directory or require a new host task.

Separation of duties applies from the first refinement step. The orchestrator
never performs Product, Design, Plan, Build, Evaluate, or Engineering work
inline. Every role emitted by `loop next` is dispatched as a real host-native
worker and collected before the orchestrator gates it. On Codex use the exact
`task_name`, role instructions, standalone `role_marker`, model when non-null,
and `reasoning_effort` from the payload, following
[`../tp-go/references/codex-native-dispatch.md`](../tp-go/references/codex-native-dispatch.md).
Claude dispatches the same complete brief through its named agent. If native
dispatch is unavailable, stop as a host-capability blocker; never collapse the
personas into the orchestrator.

Two entry sides, one flow — an idea may need Product refinement, while an
existing approved requirement/design may enter later. Both still use one
loop. Do not run standalone `/tp-product` and then repeat PM inside Build.

1. **Strategic check first (significant features) — summoned, human's call.**
   Before sinking effort into a plan, the human may run the north-star review
   on the idea: `/tp-northstar` (`north-star this <idea>`). It measures the
   idea against the project's Direction / north star and returns one strategic
   note (alignment + Leverage · Reversibility · Opportunity cost · Coherence +
   the sharpest tension + proceed / eyes-open / reconsider). Cheapest reshape
   point — but summoned, not automatic, and advisory, never a gate.
   (`../tp-northstar/SKILL.md`.)
2. **Initialize once, then refine.** If the user supplied an existing R-id,
   initialize with `$TP loop init --req R-XXXX --design "<goal>"`. Otherwise
   initialize with `$TP loop init --design "<goal>"`; do not create a
   requirement first. Call `loop next` and dispatch its `tp-product` worker;
   that worker uses `$TP req new` with functional, NFR-by-lens AND acceptance
   criteria — `--depends R-YYYY` for every
   requirement this one builds on (product dependencies are graph edges,
   not prose), and repeatable
   `--contract provides|consumes|changes:NAME` for every named API, event,
   data, trust, or runtime boundary. `$TP req score` — close every gap BEFORE
   planning. Architecture & system design input belongs here (it's
   always-on in the lens engine, starting at the spec).
3. **The graph carries both sides and gates Ready/Done.**
   `$TP graph scan` if the repo is new to taskplane. From here the loop
   maintains the product↔engineering graph mechanically: at the plan gate
   each task's requirement is linked to the modules its scope intends to
   touch (`planned` edges) and the task is annotated with its blast radius
   plus any OTHER requirements whose surface it overlaps — the consolidated
   authorization packet presents the plan with both, and the executor's contract briefing
   carries them. New modules must be declared, and distributed work must
   name its contract boundary. Before engineering review the links are
   TRUED-UP to what the build actually changed (`realizes` edges) and the graph is
   rescanned, so evaluation checks the diff against the product surface
   (`affected_requirements` in the impact payload: whose criteria need
   re-checking) and the next feature's contracts start from reality.
   Manual joins when needed: `$TP graph link --req R-XXXX --files …`,
   `$TP graph contract NAME --provider MODULE --consumer MODULE`, and
   `$TP graph edge` for runtime deps static analysis can't see. Between
   distributed entities, stop at `contract:`/`resource:`; do not pull remote
   implementation details into the local graph.
4. **Show the spec when visual feedback changes the decision.** For UI or
   interaction work, render a visual mock from the acceptance criteria before
   building. For backend/API/infrastructure work where a mock adds no signal,
   record a one-line skip reason and use the Design dependency/sequence visual
   only when it materially clarifies the approach. Never generate a decorative
   dashboard merely to satisfy this step.
5. **Settle the HOW when complexity earns it.** Add `--design` before Plan
   when the feature crosses modules/services, changes an API/event/data/runtime
   contract, has meaningful alternatives, is costly to reverse, or carries
   migration/security/operational risk. This produces a mechanically complete Design
   Contract: alternatives, selected approach, proposed graph overlay, named
   contracts, bounded depth, Design DoR/DoD, acceptance-to-validation map,
   failure/rollout evidence, and a conditional technical visual. Small,
   local, reversible work with an obvious implementation can skip this phase.
   This is distinct from the product/UI mock below: Design settles the
   technical HOW; the mock makes user-facing behavior inspectable.
6. **Loop, governed.** Continue the loop already initialized in step 2; do
   not initialize a second loop. Drive as in `/tp-go`: Product → optional
   design → plan → consolidated human authorization → contracted build (TDD, budgets)
   → evaluate → selective engineering review → visual sign-off.
   Apply the mandatory native-dispatch rule above to every emitted role and
   wait for each result before the orchestrator advances it.
   Evaluate and engineering review share one canonical review context per
   immutable change: diff, graph blast radius, requirements/contracts, DoR,
   DoD, and one complete lens disposition. Only the mapped deep lenses plus at
   most one light sweep run; agents consume scoped references and never derive
   their own diff or graph.
   The dashboard is auto-refreshed by gate/next and the payload carries its
   path. Reuse that path as progress state; do not call `dashboard`, render,
   or acknowledge it on internal transitions. Deliver and acknowledge it once
   when the payload marks a human gate, where visualization is the interface
   the human governs through (the graph tab shows
   the product layer: requirements ↔ modules, depends edges, shared-surface
   warnings).
   When Design exists, Plan must cover every approved module, contract, graph
   boundary, and acceptance mapping; Review must report conformance against
   the approved fingerprint and stop on ANY recorded drift (a human-accepted
   deviation must be moved to `accepted_drift` with drift, reason, and
   accepted_by — it is rendered at sign-off, never silent).
7. **A/B variants (when the design space is wide).** Build the SAME
   requirement two deliberate ways — different UX, different architecture,
   or both. Full procedure: `references/variants.md`. In short: variants
   are scope-identical so they never merge — one governed agent per
   variant in its own worktree + contract, same acceptance criteria, then
   an evaluation compare, a side-by-side RENDER of both (live screenshots
   beat mocks), and a human SELECTION gate that replaces the merge. Refine
   the winner (often a hybrid: one variant's engine, the other's face).
8. **Finish like every loop:** run the one-shot Retro after sign-off; it
   records lessons and trues up the graph before `done`. Record chosen debt
   and commit the KB — the next feature starts smarter, and its contracts
   inherit an accurate map of who owns what.

Human gates are non-negotiable: consolidated pre-implementation authorization,
selection if A/B, and final sign-off with the feature rendered — never a diff
alone. Product, Design, and Plan remain mechanical fail-closed checks rather
than separate ceremonial approval stops.
