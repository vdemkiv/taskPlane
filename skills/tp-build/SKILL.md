---
name: tp-build
description: "The new-feature flow of taskplane — use when the goal is to BUILD something new: 'build a new feature', 'add X to the app', 'prototype this idea', 'build it as A/B variants', 'explore two approaches', 'greenfield this'. Enters from the product side (an idea to spec), the engineering side (a tech design to realize), or both. Front-loads a summoned north-star (strategy) check and specification refinement, renders the feature visually BEFORE and AFTER building, and can build the same requirement as competing A/B variants in isolated worktrees with a human selection gate. Same enforced contracts and full lens catalog as every taskplane flow."
---

# /tp-build — new features, refined first, seen always

`TP=python3 "${CLAUDE_PLUGIN_ROOT}/taskplane/tp.py"`. Building new is where
agents waste the most — vague specs, invisible progress, one unexamined
design. tp-build inverts that: **refine before you plan, see before you
sign, and when the design space is wide, build it twice and choose.**

Two entry sides, one flow — from the PRODUCT side (an idea that needs a
spec: run `/tp-product` refinement first) or the ENGINEERING side (a tech
design that needs realizing: capture it as an R-record with technical
acceptance criteria). Both sides for anything user-facing and structural.

1. **Strategic check first (significant features) — summoned, human's call.**
   Before sinking effort into a plan, the human may run the north-star review
   on the idea: `/tp-northstar` (`north-star this <idea>`). It measures the
   idea against the project's Direction / north star and returns one strategic
   note (alignment + Leverage · Reversibility · Opportunity cost · Coherence +
   the sharpest tension + proceed / eyes-open / reconsider). Cheapest reshape
   point — but summoned, not automatic, and advisory, never a gate.
   (`../tp-northstar/SKILL.md`.)
2. **Refine until it forecasts clean.** `$TP req new` with functional,
   NFR-by-lens AND acceptance criteria — and `--depends R-YYYY` for every
   requirement this one builds on (product dependencies are graph edges,
   not prose); `$TP req score` — close every gap the forecast names BEFORE
   planning. Architecture & system design input belongs here (it's
   always-on in the lens engine, starting at the spec).
3. **The graph carries both sides — build it, maintain it, use it.**
   `$TP graph scan` if the repo is new to taskplane. From here the loop
   maintains the product↔engineering graph mechanically: at the plan gate
   each task's requirement is linked to the modules its scope intends to
   touch (`planned` edges) and the task is annotated with its blast radius
   plus any OTHER requirements whose surface it overlaps — the human
   approves the plan seeing both, and the executor's contract briefing
   carries them. At the engineering review the links are TRUED-UP to what
   the build actually changed (`realizes` edges) and the graph is
   rescanned, so evaluation checks the diff against the product surface
   (`affected_requirements` in the impact payload: whose criteria need
   re-checking) and the next feature's contracts start from reality.
   Manual joins when needed: `$TP graph link --req R-XXXX --files …`,
   `$TP graph edge` for runtime deps static analysis can't see.
4. **Show the spec.** Render a visual mock of the feature from the
   acceptance criteria (self-contained HTML via `mcp__visualize__show_widget`)
   BEFORE building — the human corrects a mock in seconds; a built feature
   costs a fix cycle. State what's assumed.
5. **Loop, governed.** `$TP loop init --req R-XXXX [--parallel]` and drive
   as in `/tp-go`: plan → human approval → contracted build (TDD, budgets)
   → evaluate → engineering review (full catalog) → visual sign-off.
   Dashboard at every transition (auto-refreshed by gate/next — the payload's `dashboard` field points at the fragment; the step journey + agent→model stats ride along) — visualization is not decoration here,
   it IS the interface the human governs through (the graph tab now shows
   the product layer: requirements ↔ modules, depends edges, shared-surface
   warnings).
6. **A/B variants (when the design space is wide).** Build the SAME
   requirement two deliberate ways — different UX, different architecture,
   or both. Full procedure: `references/variants.md`. In short: variants
   are scope-identical so they never merge — one governed agent per
   variant in its own worktree + contract, same acceptance criteria, then
   an evaluation compare, a side-by-side RENDER of both (live screenshots
   beat mocks), and a human SELECTION gate that replaces the merge. Refine
   the winner (often a hybrid: one variant's engine, the other's face).
7. **Finish like every loop:** retro, debt recorded, graph trued-up and
   committed with the KB — the next feature starts smarter, and its
   contracts inherit an accurate map of who owns what.

Human gates are non-negotiable: plan approval, (selection if A/B), final
sign-off with the feature rendered — never a diff alone.
