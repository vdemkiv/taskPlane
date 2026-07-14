---
name: tp-help
description: "Use when the user asks how taskplane works or how to get started: 'taskplane help', 'how do I use taskplane', 'what can taskplane do', 'taskplane tour', 'getting started with taskplane', 'what is taskplane'. Gives the mental model, the quickstart, and which skill to reach for."
---

# /tp-help — the guided tour

Explain conversationally (adapt to what they asked; don't dump everything):

**The mental model (30 seconds).** Agents are fast; taskplane makes them
accountable. Every agent works inside a Task Contract — what files it may
touch, which tools it may use, which commands are denied — enforced by a
hook BEFORE actions run, not by trust. Work flows through a loop with two
human gates (plan approval, final sign-off), every step is reviewed by
context-chosen lenses, and everything learned lands in a knowledge base so
the next task starts smarter and cheaper.

**Getting started (walk them through it live if they want):**

0. Brand-new / nothing attached? `/tp-go` runs a **cold-start check** first
   (`tp onboard`) and shows an onboarding dashboard that walks you through
   connecting a folder, putting it under git, and initializing taskplane —
   so you're never staring at a blank slate wondering where to point it.
1. `/tp-go <goal>` in a connected folder — sets the project up on first run,
   then: requirement → refinement score + forecast → plan → THEIR approval →
   governed build (parallel if tasks are independent) → lens reviews →
   engineering synthesis → THEIR sign-off → retro.
2. `/tp-status` anytime — where things stand and who's waiting on whom.
3. `/tp-product` — the WHAT seat: author/refine/score requirements,
   change requests, product decisions and debt.
4. `/tp-engineering` — the SOUND seat: read-only review with the full
   lens catalog (architecture & system design always on), impact,
   verdicts, retro, sign-off recommendation.
5. `/tp-northstar` — the STRATEGY lens, summoned on demand: measures a
   task/diff/idea against the project's north star and returns one advisory
   note (alignment + Leverage · Reversibility · Opportunity cost · Coherence).
   Never a gate.
6. `/tp-build` — new features: a north-star check + spec refinement first, visual mock
   before building, optional A/B variants with a human selection gate.

**When they ask "what if the agent goes rogue":** show, don't tell — an
out-of-scope write or a denied command (`git push`) gets blocked with a
reason and traced to `.taskplane/trace.jsonl`. That block message is the
product working.

**The whole surface is 7 commands** — say what you want in plain words
and the right one triggers (`/tp-go` alone covers most days: it picks up
whatever you prompt and routes to the right persona itself):

| Say | Command | Does |
|---|---|---|
| "build X" / "set up taskplane" / anything | `/tp-go` | the whole governed loop — routes to the personas below as needed |
| "spec this" / "refine the requirement" / "change request" | `/tp-product` | the WHAT seat: requirements, scores, product decisions |
| "new feature" / "prototype this" / "build it as A/B variants" | `/tp-build` | a north-star check + refinement first, visual mock before build, A/B variants with a selection gate |
| "review this" / "security review" / "what depends on X" / "run the retro" | `/tp-engineering` | the SOUND seat: full lens catalog (architecture always on), impact, verdicts, retro |
| "north-star this" / "should we build this" / "does this serve the direction" | `/tp-northstar` | the STRATEGY lens, summoned & advisory: alignment vs the north star + Leverage · Reversibility · Opportunity cost · Coherence |
| "where are we" | `/tp-status` | loop, tracks, requirements, debt |
| "how does X work" | `/tp-help` | this tour + concept explainers |

**Two seats, one bar.** tp-product asks "does it deliver the
requirement?"; tp-engineering asks "is it sound?" — deliberately separate
so definition is never graded by its own author. Both apply the same lens
catalog, and architecture & system design is always on — every code
change gets at least a light pass, because system shape is governance,
not taste. Neither seat edits code — that's the loop's job.

Concepts on request (don't dump): gates → `references/gates.md`;
contracts → `references/contracts.md`; roles & the PM handoff →
`references/roles.md`, `references/product-manager.md`. Power users: the
full CLI is `python3 "${CLAUDE_PLUGIN_ROOT}/taskplane/tp.py" --help`.

**Licensing if asked:** free and open source under Apache License 2.0 — any
use, personal or commercial (see `LICENSE`).
