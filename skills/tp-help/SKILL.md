---
name: tp-help
description: "Use when the user asks how taskplane works or how to get started: 'taskplane help', 'how do I use taskplane', 'what can taskplane do', 'taskplane tour', 'getting started with taskplane', 'what is taskplane', 'I installed taskplane, now what', 'taskplane is not doing anything'. Gives the mental model, the quickstart, and which skill to reach for."
---

# /tp-help — the guided tour

**FIRST, always:** run `python3 "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/taskplane/tp.py" onboard --json`
for the current folder. If `ready` is **false**, do NOT open with the tour —
open with setup: tell them this folder isn't set up yet, name the ONE missing
piece from `next_action` (folder / git / init), and continue directly with the
`/taskplane` cold-start procedure. Walk folder → git → init, asking before any
git initialization or commit; ask whether knowledge stays private or shared
with the team and show the model-tier map. THEN give whatever tour they asked
for. A just-installed user's real question is "now what?" — answer that before
explaining concepts.

If the onboarding report includes `artifacts`, read the latest snapshot before
reconstructing prior plans, findings, graph context, or progress. It is the
durable handoff from an earlier session or teammate, not optional decoration.

Explain conversationally (adapt to what they asked; don't dump everything):

**The mental model (30 seconds).** Agents are fast; taskplane makes them
accountable. Every agent works inside a Task Contract — what files it may
touch, which tools it may use, which commands are denied — enforced by a
hook BEFORE actions run, not by trust. Complex work can add a read-only Design
phase with its own human approval before Plan; delivery still stops at plan
approval and final sign-off. Every step is reviewed by
context-chosen lenses, and everything learned lands in a knowledge base so
the next task starts smarter and cheaper. Requirements and plans carry the
dependency graph into Ready and Done. Workers submit fingerprinted evidence;
only the orchestrator can ask the engine to advance a stage.

**What's new in v2.11 — the lens router you designed is now the one that
runs.** taskplane has scored every lens against the actual diff since v2.4 —
deep, light, or n/a with machine-checkable negative evidence — and the CLI
never asked for it: `lens route` and `lens dispatch` took the old glob path,
and the review skill passed `--all`, which switches the engine off by
construction. So every review ran all 26 lenses. It no longer does: on a Go
type change plus a docs edit the engine routes 2 deep, 4 light, and marks 20
n/a, and the dashboard shows all 26 dispositions with the reason each one
did not run. Coverage is disclosed, not spent.

**In v2.10 — nine defects a real upstream repo found.** v2.9 was
run end-to-end against a live PR in someone else's 256-module repo, and the
worst defect was silent: the findings headline said `0 high` while the file
carried a `class: regression` the engine's own gate blocks. The headline now
reads the blocking set off the engine (`1 BLOCK (1R·0H·1P·0O)`) instead of
counting severities. Parallel lens dispatch also did not survive more than
one agent — six sibling lens contracts intersected to the empty set, so 4 of
6 lenses wrote nothing and the wave board read 2/6 for a finished review;
sibling waves now merge their write-allows and sum their budgets. And the
graph could not see intra-repo Go at all, because a root `go.mod` was skipped
— `graph impact` reported 2 modules on a repo with 256.

**In v2.9 — the flow's artifacts are no longer skippable.**
A review now records what it OWES you before the work starts (`tp new --owes
review`): the lens wave board re-rendered after dispatch, and the product's
own dependency view. Until each has been shown, `tp dod` and `tp loop submit`
are refused at the hook — never an edit, a test, or any part of doing the
work, only the act of declaring it finished. `tp ack --status` lists what is
outstanding; `TASKPLANE_OBLIGATIONS=off` opts out. Renders are also now
OBSERVED at the hook with their content fingerprint, so showing a substitute
for the engine's own view is recorded as one.

**In v2.8 — the dependency graph now describes your codebase.**
Reviews lean on the graph to decide what a change touches, and until now
nothing scored whether the graph was RIGHT. An accuracy harness against four
hand-authored repo profiles found four defects and all four are fixed: module
ids come from your build manifests where you declare them (npm workspaces and
go.mod, so a monorepo module is `@acme/ui` rather than an invented `ui`); a
nested `src/` no longer renames a module after a convention or merges two
sibling apps into one node; markdown skills, agents, lenses, SQL, IaC and CI
are first-class nodes with their files, so a repo whose product is not source
code stops being invisible; and components that talk by NAMING each other
finally have edges. On this repo that is 6 modules and 4 internal edges before,
28 and 120 after. A repo can also declare which trees are not its product code
in `components.yaml`. Also in v2.8: a whole-codebase review no longer fans out
26 subagents under a cap of 8, and content detectors stopped firing on
documentation that merely DESCRIBES what they look for.

**What routing looks like now (v2.7).** Reviews don't run all 26 lenses
blindly: the router scores each lens against the ACTUAL diff — paths,
content, density, the dependency graph — and stage profiles (design/build/
review) narrow the candidates, so a typical review runs 5-8 lenses deep with
the rest as evidenced light passes or n/a-with-proof ("0 i18n markers").
Security and architecture are floored, never dropped. The graph can also be
DECOMPOSED into components (`tp graph scan --decompose`): each component
carries its own lens map, a diff routes the components it touches, and any
routing failure only ever WIDENS coverage (component → module → full
catalog). Test fixtures no longer inflate routing (×0.25 discount). Every
Nth review runs as a full-catalog audit that diffs findings against the
routing — a finding on an n/a'd lens auto-files as a router regression and
blocks sign-off. The DoD can run a graph-scoped regression gate: the blast
radius's tests at the change's baseline vs now; only was-green-now-red
blocks. Full detail: `docs/routing-and-flows.md`.

**What's new in v2.6 — the loop stopped paying twice.** A month of dogfooding made per-task cost grow about thirteenfold, as the product of four independent growths that nobody was measuring together. Three changes, none of which weakens a gate. The DoD test command is now CITED rather than re-run when the same command already completed over byte-identical content under the same engine and env — a phase used to run the suite about six times per agent; it now runs once per tree state, and every citation is an auditable trace event where 'I ran the tests' used to be narration no gate could check. `tp loop evidence` hands an evaluator the suite result, the diff, and the exact criteria, lens and graph obligations in ONE call, with judgment slots empty — a bundle submitted unchanged is refused. And `scripts/ci_loop_cost.py` pins what the engine mandates per task, so adding a proof obligation costs a line and a sentence on the record instead of quietly costing everyone time.

**Lenses 2.0 (v2.7).** All 26 lenses were rewritten against current industry
practice, each reviewed on its own rather than in a batch, with every source's
authority *and* its limitation recorded — and four superseded citations caught
and corrected in the process. The largest fix was not content but routing:
twelve of the twenty-six could not fire on the change class they exist to
judge, so their declared blockers were unreachable by construction. The
security lens could not see `.yml`, `.lock` or `Dockerfile`, so a compromised
CI workflow never reached the lens that owns supply-chain risk. The i18n lens
fired only on translation files, so twenty hard-coded strings in a component
never triggered it. All twelve are closed, the routing data is now validated
at generation time so an invalid task type fails loudly instead of becoming a
silent dead key, and the review fan-out is pinned in CI like everything else
that costs.

**Reviews say what breaks (v2.6).** A finding may block a gate only if it carries a claim: a concrete trigger, the wrong outcome observed, and a repro someone else can run. Commentary is still welcome and still recorded — it just stops rendering as a bug, which is what trained everyone to skim reviews.

**Waves as workflows (Claude) — Task dispatch everywhere.** On Claude Code
hosts with Dynamic Workflows, the review fan-out and the execute/evaluate/
fix waves can each run as ONE journaled, resumable workflow
(`--emit workflow|task|auto` on `tp lens dispatch`, `loop wave`,
`loop next`; opt in with TASKPLANE_WORKFLOWS=1). The Task-dispatch path
stays byte-identical and is the only path on Codex — same contracts, same
gates, same evidence; workflows are an optimization, never a dependency.
Human gates always stay conversation-level.

**Native Codex agents.** On Codex, taskplane briefs carry a stable native
`task_name`, the taskplane role, optional model, and tier-derived reasoning
effort. Independent briefs run concurrently; Codex waits for every requested
result before synthesis and can interrupt a stalled or mis-scoped agent.
`SubagentStart`/`SubagentStop` trace lifecycle, while PreToolUse and the evidence
gates remain the enforcement boundary. For long runs the user may start
`/goal`; it keeps the work running but grants no extra authority and skips no
taskplane gate.

**Getting started (walk them through it live if they want):**

0. Brand-new / nothing attached? `/taskplane` runs a **cold-start check** first
   (`tp onboard`) and shows an onboarding dashboard that walks you through
   connecting a folder, putting it under git, and initializing taskplane —
   so you're never staring at a blank slate wondering where to point it.
1. `taskplane design <goal>` — turns a refined requirement and current code
   into alternatives plus an approvable Design Contract: modules, dependency
   overlay, named contracts, bounded depth, risks/rollout, validation map, and
   a useful technical visual when one helps. It changes no product code.
2. `taskplane build <goal>` in a connected folder — sets the project up on first run,
   then: requirement → refinement score + forecast → plan → THEIR approval →
   governed build (parallel if tasks are independent) → lens reviews →
   engineering synthesis → THEIR sign-off → retro.
3. `taskplane review <target>` — read-only full-lens review with dependency
   impact and requirement/contract evidence.
4. `taskplane status` anytime — where things stand and who's waiting on whom.

Power-user routes remain available:

5. `/tp-product` — the WHAT seat: author/refine/score requirements,
   change requests, product decisions and debt.
6. `/tp-design` — the proposed HOW seat: compare approaches, declare the
   dependency/contract shape and Design DoR/DoD, then stop for approval.
7. `/tp-engineering` — the SOUND seat: read-only review with the full
   lens catalog (architecture & system design always on), impact,
   verdicts, retro, sign-off recommendation.
8. `/tp-northstar` — the STRATEGY lens, summoned on demand: measures a
   task/diff/idea against the project's north star and returns one advisory
   note (alignment + Leverage · Reversibility · Opportunity cost · Coherence).
   Never a gate.
9. `/tp-build` — new features: a north-star check + spec refinement first, visual mock
   before building, optional A/B variants with a human selection gate.

**When they ask "what if the agent goes rogue":** show, don't tell — an
out-of-scope write or a denied command (`git push`) gets blocked with a
reason and traced to `.taskplane/trace.jsonl`. That block message is the
product working.

**The normal surface is four prompts.** Specialist skills below are aliases
for people who want direct control:

| Say | Command | Does |
|---|---|---|
| "design X" / "compare approaches for X" | `taskplane design …` | the proposed HOW: alternatives, approved Design Contract, dependency/contract overlay, Design DoR/DoD, conditional technical visual; no product-code changes |
| "build X" / "set up taskplane" / anything | `taskplane build …` | the whole governed loop — routes internally as needed |
| "spec this" / "refine the requirement" / "change request" | `/tp-product` | the WHAT seat: requirements, scores, product decisions |
| "new feature" / "prototype this" / "build it as A/B variants" | `/tp-build` | a north-star check + refinement first, visual mock before build, A/B variants with a selection gate |
| "review this" / "security review" / "what depends on X" / "run the retro" | `taskplane review …` | the SOUND seat: full lens catalog (architecture always on), impact, verdicts, retro |
| "north-star this" / "should we build this" / "does this serve the direction" | `/tp-northstar` | the STRATEGY lens, summoned & advisory: alignment vs the north star + Leverage · Reversibility · Opportunity cost · Coherence |
| "where are we" | `taskplane status` | concise progress, current harness state, and decisions needed |
| "how does X work" | `/tp-help` | this tour + concept explainers |

**Four responsibilities, one bar.** Product defines the WHAT. Design proposes
the HOW before code. Build realizes an approved plan/design. Engineering
Review judges whether the result is sound and conformant. These boundaries
prevent authors from approving their own output. All use the same lens
catalog; architecture & system design is always on and the distinct
solution-design lens tests the coherence and implementability of a proposed
approach. Every code change gets at least a light architecture pass because
system shape is governance, not taste. Product, Design, and Review do not edit
product code; Build does so only under its approved contract.

Concepts on request (don't dump): gates → `references/gates.md`;
contracts → `references/contracts.md`; roles & the PM handoff →
`references/roles.md`, `references/product-manager.md`; routing v2,
decomposition, waves & audits → `docs/routing-and-flows.md`; install paths
by account type (org members cannot install from GitHub — admin catalog or
file upload) → `README.md` Install section. Power users: the
full CLI is `python3 "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/taskplane/tp.py" --help`.

**Licensing if asked:** free and open source under Apache License 2.0 — any
use, personal or commercial (see `LICENSE`).
