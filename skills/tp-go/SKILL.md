---
name: tp-go
description: "The internal delivery driver behind the taskplane facade — goal-shaped asks ('build X', 'fix X', the word taskplane) land on the facade, which routes here. Reach for this skill directly only when the user explicitly drives the loop: 'start governed work', 'run the loop', 'run tasks in parallel', 'dispatch the wave', 'run the retro', 'log tech debt'. Drives governed delivery end to end, routing to the right persona — tp-product (define WHAT), tp-design (propose HOW), tp-build (realize), tp-engineering (validate) — with every step under an enforced contract and every human gate honored."
---

# /tp-go — goal in, governed delivery out

`TP=python3 "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/taskplane/tp.py"`. Drive the whole loop;
pause ONLY at the human gates. Follow each step's returned `instruction`.

This is the internal delivery driver behind the user-facing `taskplane`
facade — user phrasing like "build X" arrives via the facade and routes
here. Keep role names, CLI choreography, graph policies, and evidence files
out of the user's way unless they ask. Do not simplify any of them for
agents. The command forms in this skill and the engine's returned action are
authoritative; do not call `$TP --help`, inspect taskplane source/tests, or
run exploratory status/list commands during the normal path.

**One owner per phase.** The orchestrator never authors Product, Design,
Plan, Build, Evaluate, or Engineering artifacts inline. It initializes the
loop, dispatches the exact role emitted by `loop next`, waits, and applies the
mechanical gate. A goal with no existing R-id starts the loop without `--req`:
the first PM action creates exactly one complete requirement and spec. Attach
that returned R-id on the PM gate with `loop gate pass --req R-XXXX`. Never
run standalone Product refinement first and then repeat it inside the loop.

**One selective evidence kernel.** Review, Evaluate, and final EM use one
pinned diff, graph-quality/blast-radius record, requirements/contracts and
DoR/DoD envelope, one complete 26-lens disposition, and leased results.
Dispatch exactly the deep slots plus at most one light sweep. An
`impact_incomplete` run dispatches nobody. Lenses consume scoped artifact
references and never rederive diff, graph, routing, or runnability.

**Model tiers.** Each `loop next` payload and each `lens dispatch` brief carries
an exact Codex-safe `task_name`, the taskplane `role`/`agent`, a `model` (a
concrete id, or `null` = inherit), and tier-derived `reasoning_effort`.
On Codex, pass those exact native dispatch fields and omit `model` when null.
A planner marks a simple task
`"model": "cheap"` in tasks.json to route just that task cheaper. Never pin a
model in agent frontmatter — the pin lives only at the dispatch call, which is
what keeps taskplane portable. Full detail: `discipline/model-tiers.md`.
The complete Codex spawn/wait/interrupt procedure is mandatory:
`references/codex-native-dispatch.md`.

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

**Show decision points, not internal chatter.** Every transition already
refreshes `.taskplane/dashboard.html`; do not call `$TP dashboard` or `ack`
after each internal step. Relay the returned `HEADLINE`/dashboard path by
reference while agents are working. Render the engine-authored HTML verbatim
only before a human gate, an explicit status request, or a long fan-out where
progress materially helps. Acknowledge only a dashboard actually rendered to
the human. Never paste the HTML into model context or regenerate its graph.

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
2. **Initialize once:** when the user supplied an existing R-id, run
   `$TP loop init --req R-XXXX "<goal>"`. Otherwise run `$TP loop init
   "<goal>"`; the PM step owns the first requirement/spec. Never run a
   standalone `req new` before this loop. Add `--design` for a
   complex/risky/contract-changing or explicitly requested proposed-HOW phase;
   add `--design-only` when the deliverable is the approved design itself;
   add `--parallel` when the plan will have independent tasks; use `--spec
   path` only for an existing complete spec.
3. **Dispatch, never impersonate:** call `$TP loop next` once for the current
   step and dispatch the named role under its already-active contract. On
   Codex, follow
   `references/codex-native-dispatch.md`: use the exact `task_name`, model and
   `reasoning_effort`, standalone `role_marker`, and complete
   `role_instructions` file plus action payload. Wait with bounded native
   waits and collect the final result. Do not perform the role inline, call
   `loop next` again while it is running, or replace its contract. The PM
   worker returns one R-id; attach it on its mechanical gate with
   `$TP loop gate pass --req R-XXXX`. Product/planner return artifacts; only
   execute/fix/evaluate/engineering workers submit. Design writes
   `design/contract.json` and
   `design/design.md`, compares alternatives, declares a proposed graph
   overlay with bounded contract-level boundaries, runs the mandatory
   solution-design lens, and stops for human approval. It never changes code
   or the as-built graph. Plan writes plan/tasks.json
   (each task: id, scope, tests, req, deps, contracts, `new_modules` when
   applicable, and typed `impact_policy`), execute builds TDD-first
   (`discipline/tdd.md`) honoring the primed lenses, evaluate proves
   criteria + runs routed lenses and dispositions graph impact — its briefs
   are routed with `stage="build"` (route v2: build-profile candidates
   scored against the wave's real diff, cap-8 budget, floors, evidenced
   n/a; when a component layer exists — `tp graph scan --decompose` — the
   touched components assemble the candidates and each routed lens names
   its proposers). The engineering review uses the same canonical review
   context and exact selective routing decision, and every Nth completed
   review (default 5,
   `TASKPLANE_AUDIT_EVERY`) also runs the full-catalog audit sweep: a
   finding on a lens the router marked n/a auto-files as a router
   regression that blocks sign-off. Full routing detail:
   `docs/routing-and-flows.md`.
   Execute/fix/evaluate/engineering workers
   end with `loop submit` and stop; the orchestrator alone calls `loop gate`
   and trusts only the engine's recomputed evidence — the canonical
   submit/gate/human-checkpoint invariants live in
   `../taskplane/references/harness-rules.md`. Product/planner return their
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
   **Stage emitters:** `$TP loop wave --emit workflow|task|auto` (execute)
   and `$TP loop next --emit workflow|task|auto` (evaluate/fix) pick the
   dispatch rail — the review fan-out has the same switch on
   `$TP lens dispatch`. On a Claude Code host with Dynamic Workflows the
   whole stage can run as ONE journaled, resumable workflow run; the
   Task-dispatch payload stays the mandatory fallback, byte-identical, and
   the only Codex path. Detection is `workflow_available()` alone (opt in
   with `TASKPLANE_WORKFLOWS=1`; any of 0/false/no/off disables). Workflows
   are transport only: same contracts, same briefs, same gates — no gate is
   reachable only via workflows, and human gates stay conversation-level.
   When all variants pass, the loop pauses at the native `selection` gate:
   present both variants rendered side by side, then
   `$TP loop select <variant|hybrid> --note "why"` on the human's choice —
   full procedure in `../tp-build/references/variants.md`.
   On Codex, spawn independent entries concurrently, wait for and collect all
   requested results, and interrupt/escalate stalled or mis-scoped agents as
   specified in `references/codex-native-dispatch.md`. For a long governed run,
   recommend optional user-started `/goal`; Goal mode never replaces a gate.
6. **Finish:** after sign-off run the retro per `references/retro.md`,
   then `discipline/finishing-work.md` (debt, graph rescan, track close).
