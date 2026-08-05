---
name: tp-go
description: "The single entry point for governed work — use when the user states a goal and wants taskplane to handle everything: 'go build X', 'implement X with taskplane', 'start governed work', 'run the loop', 'set up taskplane', 'run tasks in parallel', 'dispatch the wave', 'run the retro', 'log tech debt'. Picks up whatever is prompted and executes it as far as possible, routing to the right persona — tp-product (define WHAT), tp-design (propose HOW), tp-build (realize), tp-engineering (validate) — with every step under an enforced contract and every human gate honored."
---

# /tp-go — goal in, governed delivery out

`TP=python3 "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/taskplane/tp.py"`. Drive the whole loop;
pause ONLY at the human gates. Follow each step's returned `instruction`.

This is the internal delivery driver behind the user-facing `taskplane`
skill. Keep role names, CLI choreography, graph policies, and evidence files
out of the user's way unless they ask. Do not simplify any of them for agents.

**Model tiers.** Each `loop next` payload and each `lens dispatch` brief carries
a `model` (a concrete id, or `null` = inherit the session model) resolved from a
capability tier — mechanical steps/tasks/sweeps run cheaper, hard reasoning runs
stronger. When you dispatch the role or lens agent, pass that `model` to the
Agent tool's `model` param (omit it when `null`). A planner marks a simple task
`"model": "cheap"` in tasks.json to route just that task cheaper. Never pin a
model in agent frontmatter — the pin lives only at the dispatch call, which is
what keeps taskplane portable. Full detail: `discipline/model-tiers.md`.

**Four user intents, one driver — route by the ask, combine freely:**

| The ask is about… | Persona | Skill |
|---|---|---|
| WHAT to build, requirements, change requests | tp-product | `../tp-product/SKILL.md` |
| HOW a new feature or approach should work before code | tp-designer | `../tp-design/SKILL.md` |
| BUILDING something new (spec-first, visual-first, optional A/B variants) | tp-build | `../tp-build/SKILL.md` |
| whether built work is SOUND — review, impact, sign-off, retro | tp-engineering | `../tp-engineering/SKILL.md` |

The loop dispatches them automatically (`pm` = tp-product, `design` =
tp-designer, `em` = tp-engineering). Reach for tp-design when the proposed HOW
needs alternatives, dependency/contract decisions, or rollout evidence before
implementation; reach for tp-build whenever the goal is a new feature rather
than a fix or review.

**SHOW THE WORK — render the live dashboard at every transition.** Use the
host's inline HTML/widget capability when available. When it is not, relay the
`HEADLINE:` and provide `.taskplane/dashboard.html` as the dashboard artifact;
never pretend unavailable widget buttons were shown.

**Progress-first, not result-only.** Render BEFORE a burst of work, not
just after it. When you're about to dispatch agents (a parallel wave, a
lens fan-out), render the "starting" board FIRST so the person sees the
work forming — then render again as it lands. A dashboard that only
appears at the end is the failure mode; the whole point is to watch
progress. If a step will take several tool calls, show the board going in.

After each `loop next`, `loop submit`, `loop gate`, `loop wave`, and
`loop approve`:
0. **The fragment is already on disk.** Every successful `loop gate` /
   `loop next` refreshes `.taskplane/dashboard.html` and returns a
   `dashboard` field in its JSON — rendering is part of the flow, not an
   optional extra call. Read that file (or run `$TP dashboard`) and SHOW it;
   never skip a transition. The board now also carries the **step journey**
   (click any traversed step for its execution + decision detail) and an
   always-on **stats band with the agent→model table** (who ran which
   step/lens on which model — expected vs dispatched).
1. `$TP dashboard` — prints the mission-control HTML fragment. Four tabs:
   **loop** (governance rail PM→Design→Approve Design→Plan→Approve→Build→EM→Sign-off→Done,
   with Design omitted for simple direct builds; inside
   Build, one lane per task showing its own build → evaluate ⟲ fix
   mini-pipeline — parallel lanes visible side by side — plus live feed and,
   at `plan_approval`/`signoff`/`escalated`, gate buttons wired to
   `sendPrompt`), **stats** (agents/steps/waves/fixes/blocks + KB counts),
   **graph** (hubs + blast radius of the current scope), **context**
   (requirement, acceptance criteria, routed lenses, recent decisions, debt).
2. Put the decision context in TEXT first (what happened, what's the call),
   THEN call `mcp__visualize__show_widget` when available, with that fragment
   as `widget_code`, as the LAST thing in the reply. Otherwise link the
   refreshed `.taskplane/dashboard.html` artifact after the text. Title:
   `taskplane_<goal-slug>_<step>` — UNIQUE per render; a repeated title
   updates the earlier widget in place instead of drawing a new one at the
   current position.

**Render contract (v1.5.3/4) — the same flow every taskplane command uses.**
`$TP dashboard` prints a `HEADLINE:` line first — relay it to the human as
plain text, always: it is the never-skippable carrier of step + gate +
lens/graph coverage, so the status lands even if a render is skipped. For an
unusually large board use `$TP dashboard --paged` (ordered ≤14 KB pages) and
render EACH page in order via `show_widget` — never collapse them into one
giant widget and never replace them with a prose recap. The loop board's
**context tab shows the full lens catalog** (sourced from `catalog.json`, so
a newly added lens appears automatically) and the **graph tab shows blast
radius**; if the graph is empty on a polyglot repo, say so — don't omit it.

**Shared progress artifacts (v2.0.0).** Every `loop gate`/`next`/`approve`/
`retro` also snapshots the decision artifacts (dashboard, plan, findings,
graph, `HEADLINES.md`, retro) into the active store — the payload's
`artifacts.path` names the folder. On a team/enterprise store that folder is
inside `.taskplane-kb/`: COMMIT it with the work, so the org sees progress
from a fresh clone. Treat it as a context cache too — before re-deriving
plan/review/graph state, read the snapshot; it's cheaper than recomputing.

At a human gate, STOP after showing the widget or dashboard artifact. Widget
buttons can drive the next prompt where supported; otherwise ask for the same
explicit approval in conversation. Never run the loop silently.

0. **Cold start (nothing attached yet):** FIRST run `$TP onboard --json`.
   If `ready` is false, don't dive in — show the onboarding dashboard
   (`$TP onboard` prints the fragment) inline via `mcp__visualize__show_widget`
   and help with the one missing piece its `next_action` names:
   `attach_folder` → the user needs to connect/open a folder or give you a git
   URL to clone. In Codex CLI, start from the repo directory; in the desktop
   app, open/create a local environment for that repo and start a new task
   after installation. Then re-check. `init_git` → offer to `git init && git add -A &&
   git commit` for them (gates need a snapshot); `tp_init` → run step 1.
   The buttons drive this via `sendPrompt`. Don't guess a workspace — a
   governed run needs a real folder + a git commit, and this is where a
   brand-new user gets them in place.
   If the report includes `artifacts`, read the latest snapshot before
   re-deriving plan, review, graph, or progress state; it is the durable
   cross-session/team handoff.
1. **Setup (once a folder + repo exist):** if `knowledge/context/` is
   missing, run `$TP init` yourself (details: `references/setup.md`) and fill
   the three context docs from the conversation — only ask what you can't
   infer.
2. **Requirement (the product phase):** author it WITH the user and score
   it — full procedure in `../tp-product/references/requirements.md`
   (record, score, refine on gaps, quick vs full with tracked debt). For a
   NEW FEATURE, follow `../tp-build/SKILL.md` instead: a north-star check first for
   significant ones, refine until the forecast is clean, render a visual
   mock of the spec BEFORE building.
3. **Loop:** `$TP loop init --req R-XXXX "<goal>"` (add `--design` for a
   complex/risky/contract-changing or explicitly requested proposed-HOW phase;
   add `--design-only` when the deliverable is the approved design itself;
   add `--parallel` when
   the plan will have independent tasks; `--spec path` if a spec exists).
   Then repeat `$TP loop next` and DO what its `instruction` says, playing
   the named role under its activated contract. On Codex or any host that
   does not register `agents/` as named roles, dispatch a general subagent
   with the action payload's `role_instructions` file; never improvise a
   reduced role prompt. Design writes `design/contract.json` and
   `design/design.md`, compares alternatives, declares a proposed graph
   overlay with bounded contract-level boundaries, runs the mandatory
   solution-design lens, and stops for human approval. It never changes code
   or the as-built graph. Plan writes plan/tasks.json
   (each task: id, scope, tests, req, deps, contracts, `new_modules` when
   applicable, and typed `impact_policy`), execute builds TDD-first
   (`discipline/tdd.md`) honoring the primed lenses, evaluate proves
   criteria + runs routed lenses and dispositions graph impact, the
   engineering review synthesizes. Execute/fix/evaluate/engineering workers
   end with `loop submit`; the orchestrator alone calls `loop gate` and trusts
   only the engine's recomputed evidence. Product/planner return their
   artifacts for the orchestrator's mechanical gate.
   If a Design Contract is approved, each proposed dependency edge is copied
   into the owning task's `design_edges` as `FROM->TO:KIND`; the plan gate
   checks the complete set along with modules, contracts, depth, and criteria.
4. **Human gates:** at `design_approval` present the selected approach,
   alternatives, modules/edges/contracts, risks/rollout, validation map, and
   useful visual, then WAIT for the user. At `plan_approval` present the plan
   + refinement forecast and WAIT; at `signoff` present the engineering
   report and WAIT. `$TP loop approve` only on their explicit yes.
   Escalations: present options, `$TP loop resolve retry|skip|abort` on
   their choice.
   **Visual sign-off (UI changes):** if the change touched a UI (any task
   with `type: ui`, or a diff under a client/component/screen path), don't
   sign off on a diff alone — RENDER THE FIXED SCREEN. Boot the real app
   and screenshot it when possible; otherwise build a faithful,
   self-contained HTML mock of the changed view populated with mock data
   (reproduce the components' actual classes; inline the CSS — CDNs may be
   blocked), and show it inline via `mcp__visualize__show_widget` right
   above the sign-off gate so the human reviews the working result, not
   just the code. State what's mocked. The visual IS part of the sign-off.
5. **Parallel:** when `loop next` returns a wave, follow
   `references/parallel.md` (worktree + claim + one governed subagent per
   task, commit before submitting, orchestrator gate, merge on evaluate PASS — EXCEPT entries
   with `merge_on_pass: false`: those are A/B variants, never merge them).
   When all variants pass, the loop pauses at the native `selection` gate:
   present both variants rendered side by side, then
   `$TP loop select <variant|hybrid> --note "why"` on the human's choice —
   full procedure in `../tp-build/references/variants.md`.
6. **Finish:** after sign-off run the retro per `references/retro.md`,
   then `discipline/finishing-work.md` (debt, graph rescan, track close).
